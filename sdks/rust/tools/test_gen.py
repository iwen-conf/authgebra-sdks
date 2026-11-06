"""Exercise real generation/check commands in disposable output directories."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

GEN = Path(__file__).with_name("gen.py")
SPEC = GEN.parents[2] / "contracts/openapi.json"


class GenerationTests(unittest.TestCase):
    def run_gen(self, spec, output, check=False):
        return subprocess.run([sys.executable, str(GEN), str(spec), str(output)] + (["--check"] if check else []),
                              text=True, capture_output=True, check=False)

    def test_corrupt_generated_bytes_fail_without_repairing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            self.assertEqual(self.run_gen(SPEC, out).returncode, 0)
            originals = {path.name: path.read_bytes() for path in out.iterdir()}
            for filename, before, after in [
                ("models.rs", b"pub error: ApiErrorPayload,", b""),
                ("models.rs", b"pub error: ApiErrorPayload,", b'#[serde(rename = "wrong")]\n pub error: ApiErrorPayload,'),
                ("models.rs", b"Option<Option<String>>", b"Option<String>"),
                ("models.rs", b"Authenticated,", b"AuthenticatedChanged,"),
                ("admin.rs", b"pub body: models::CreateUserRequest,", b"pub body: String,"),
                ("models.rs", b"pub error: ApiErrorPayload,", b"pub error: serde_json::Value,"),
            ]:
                with self.subTest(filename=filename, mutation=after):
                    original = originals[filename]
                    self.assertIn(before, original)
                    corrupted = original.replace(before, after, 1)
                    (out / filename).write_bytes(corrupted)
                    result = self.run_gen(SPEC, out, check=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("generated artifact differs", result.stderr)
                    self.assertEqual((out / filename).read_bytes(), corrupted)
                    (out / filename).write_bytes(original)
            self.assertEqual(self.run_gen(SPEC, out, check=True).returncode, 0)

    def test_description_does_not_control_patch_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = root / "before", root / "after"
            self.assertEqual(self.run_gen(SPEC, before).returncode, 0)
            doc = json.loads(SPEC.read_text())
            doc["components"]["schemas"]["PatchRoleRequest"]["properties"]["description"]["description"] = "Updated explanation."
            changed = root / "contract.json"
            changed.write_text(json.dumps(doc))
            self.assertEqual(self.run_gen(changed, after).returncode, 0)
            def declarations(path):
                return [line for line in path.read_text().splitlines() if not line.strip().startswith("///")]
            self.assertEqual(declarations(before / "models.rs"), declarations(after / "models.rs"))

    def test_dangling_refs_and_unsupported_types_fail(self):
        for mutation in ({"$ref": "#/components/schemas/DefinitelyMissing"}, {"type": "impossible"},
                         {"not": {"type": "string"}}, {"type": "integer", "format": "unknown"}, {"type": "array"}, {"minLength": 1}, {"dependentRequired": {"a": ["b"]}},
                         {"type": "object", "additionalProperties": False}):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                doc = json.loads(SPEC.read_text())
                doc["components"]["schemas"]["CheckRequest"]["properties"]["resource"] = mutation
                changed = root / "contract.json"
                changed.write_text(json.dumps(doc))
                result = self.run_gen(changed, root / "output")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(any(message in result.stderr for message in ("unresolved schema reference", "unsupported schema", "unsupported integer format", "array needs items")), result.stderr)


if __name__ == "__main__":
    unittest.main()
