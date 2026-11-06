import pathlib
import json
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class GeneratedModelsTest(unittest.TestCase):
    def test_admin_body_is_a_named_contract_model(self):
        source = (ROOT / "src/admin_ops.zig").read_text()
        self.assertIn("body: models.CreateUserRequest", source)
        self.assertIn("errors.Outcome(models.UserDto)", source)
        self.assertNotIn("body: Json,", source.split("pub const RawAdminClient")[0])

    def test_byte_check_detects_each_generated_contract_mutation(self):
        names = ("admin_ops.zig", "models.zig", "model_roundtrip_test.zig", "models.provenance.json")
        with tempfile.TemporaryDirectory() as directory:
            sdk = pathlib.Path(directory) / "sdks/zig"
            (sdk / "tools").mkdir(parents=True)
            (sdk / "src").mkdir()
            contracts = sdk.parent / "contracts"
            contracts.mkdir()
            for name in ("gen_admin.py", "model_gen.py"):
                shutil.copyfile(ROOT / "tools" / name, sdk / "tools" / name)
            shutil.copyfile(ROOT.parent / "contracts/openapi.json", contracts / "openapi.json")
            originals = {name: (ROOT / "src" / name).read_bytes() for name in names}
            for name, content in originals.items():
                (sdk / "src" / name).write_bytes(content)

            def run():
                return subprocess.run(["python3", str(sdk / "tools/gen_admin.py"), "--check"], capture_output=True, text=True)

            self.assertEqual(0, run().returncode)
            mutations = [
                ("models.zig", b"username: []const u8,", b""),
                ("models.zig", b"username: []const u8,", b'user_name: []const u8,'),
                ("models.zig", b"Optional([]const u8)", b"[]const u8"),
                ("models.zig", b"committed_unpublished", b"modified_enum"),
                ("admin_ops.zig", b"body: models.CreateUserRequest", b"body: models.CreateRoleRequest"),
            ]
            for name, before, after in mutations:
                with self.subTest(mutation=before):
                    self.assertIn(before, originals[name])
                    altered = originals[name].replace(before, after, 1)
                    (sdk / "src" / name).write_bytes(altered)
                    self.assertNotEqual(0, run().returncode)
                    self.assertEqual(altered, (sdk / "src" / name).read_bytes(), "--check must not repair the file")
                    (sdk / "src" / name).write_bytes(originals[name])
            spec_path = contracts / "openapi.json"
            spec = json.loads(spec_path.read_text())
            prop = spec["components"]["schemas"]["PatchUserRequest"]["properties"]["email"]
            prop["description"] = "Changing human wording must not change PATCH capability."
            spec_path.write_text(json.dumps(spec))
            self.assertEqual(0, run().returncode)
            prop["x-authgebra-clearable"] = not prop.get("x-authgebra-clearable", False)
            spec_path.write_text(json.dumps(spec))
            self.assertNotEqual(0, run().returncode)
            spec["components"]["schemas"]["CreateUserRequest"]["properties"]["display_name"] = {"$ref": "#/components/schemas/DoesNotExist"}
            spec_path.write_text(json.dumps(spec))
            failure = run()
            self.assertNotEqual(0, failure.returncode)
            self.assertIn("unresolved reference", failure.stderr)
            original_spec = json.loads((ROOT.parent / "contracts/openapi.json").read_text())
            unsupported = [
                {"type": "string", "pattern": "^other$"},
                {"type": "array", "items": {"type": "integer"}, "uniqueItems": True},
                {"not": {"type": "string"}},
                {"if": {"type": "string"}, "then": {"type": "string"}, "else": {}},
                {"type": "object", "patternProperties": {"^x": {"type": "string"}}},
                {"type": "array", "prefixItems": [{"type": "string"}]},
                {"type": "integer", "format": "unknown-integer"},
                {"type": "array"},
            ]
            for schema in unsupported:
                with self.subTest(unsupported=schema):
                    candidate = json.loads(json.dumps(original_spec))
                    candidate["components"]["schemas"]["CreateUserRequest"]["properties"]["display_name"] = schema
                    spec_path.write_text(json.dumps(candidate))
                    failure = run()
                    self.assertNotEqual(0, failure.returncode)
                    self.assertTrue("unsupported" in failure.stderr or "no items" in failure.stderr)


if __name__ == "__main__":
    unittest.main()
