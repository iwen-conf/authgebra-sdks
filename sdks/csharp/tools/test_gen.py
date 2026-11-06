import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class GenerationCheckTests(unittest.TestCase):
    def test_byte_check_rejects_actual_field_and_operation_mutations(self):
        names = ("Models.g.cs", "Api.g.cs", "Models.provenance.json")
        with tempfile.TemporaryDirectory() as directory:
            sdk = pathlib.Path(directory) / "sdks/csharp"
            output = sdk / "src/Authgebra.Client/Generated"
            output.mkdir(parents=True)
            (sdk / "tools").mkdir()
            contracts = sdk.parent / "contracts"
            contracts.mkdir()
            shutil.copyfile(ROOT / "tools/gen.py", sdk / "tools/gen.py")
            for name in ("openapi.json", "openapi.sha256"):
                shutil.copyfile(ROOT.parent / "contracts" / name, contracts / name)
            originals = {name: (ROOT / "src/Authgebra.Client/Generated" / name).read_bytes() for name in names}
            for name, content in originals.items():
                (output / name).write_bytes(content)

            def run(check=True):
                return subprocess.run(["python3", str(sdk / "tools/gen.py")] + (["--check"] if check else []), capture_output=True, text=True)

            self.assertEqual(0, run().returncode)
            mutations = [
                ("Models.g.cs", b"public required string Username { get; init; }", b""),
                ("Models.g.cs", b'JsonPropertyName("username")', b'JsonPropertyName("user_name")'),
                ("Models.g.cs", b"Optional<string> Email", b"string Email"),
                ("Models.g.cs", b'new("authenticated")', b'new("modified_enum")'),
                ("Api.g.cs", b"CreateUserRequest body", b"CreateRoleRequest body"),
                ("Models.g.cs", b"Optional<AuthzResourceRef>", b"Optional<JsonValue>"),
            ]
            for name, before, after in mutations:
                with self.subTest(mutation=before):
                    self.assertIn(before, originals[name])
                    altered = originals[name].replace(before, after, 1)
                    (output / name).write_bytes(altered)
                    self.assertNotEqual(0, run().returncode)
                    self.assertEqual(altered, (output / name).read_bytes())
                    (output / name).write_bytes(originals[name])
            spec_path = contracts / "openapi.json"
            spec = json.loads(spec_path.read_text())
            prop = spec["components"]["schemas"]["PatchUserRequest"]["properties"]["email"]
            prop["description"] = "Human prose must not decide null semantics."
            spec_path.write_text(json.dumps(spec))
            self.assertEqual(0, run(False).returncode)
            generated = (output / "Models.g.cs").read_text()
            before = originals["Models.g.cs"].decode()
            semantic = lambda text: "\n".join(line for line in text.splitlines() if not line.strip().startswith("///"))
            self.assertEqual(semantic(before), semantic(generated))
            prop["x-authgebra-clearable"] = not prop.get("x-authgebra-clearable", False)
            spec_path.write_text(json.dumps(spec))
            self.assertNotEqual(0, run().returncode)
            spec["components"]["schemas"]["CreateUserRequest"]["properties"]["display_name"] = {"$ref": "#/components/schemas/DoesNotExist"}
            spec_path.write_text(json.dumps(spec))
            failure = run()
            self.assertNotEqual(0, failure.returncode)
            self.assertIn("unresolved schema reference", failure.stderr)
            original_spec = json.loads((ROOT.parent / "contracts/openapi.json").read_text())
            unsupported = [
                {"type": "string", "pattern": "^other$"},
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
                    self.assertTrue("unsupported" in failure.stderr or "missing its item schema" in failure.stderr)


if __name__ == "__main__":
    unittest.main()
