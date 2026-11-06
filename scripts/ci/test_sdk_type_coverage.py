"""Sensitivity checks for the independent schema-to-source coverage audit."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("sdk_type_coverage", Path(__file__).with_name("sdk-type-coverage.py"))
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class TypeCoverageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "sdks/contracts").mkdir(parents=True)
        (self.root / "sdks/python/src/authgebra/_generated").mkdir(parents=True)
        self.schema = {
            "type": "object", "additionalProperties": False, "required": ["id"],
            "properties": {"id": {"type": "string", "minLength": 1}, "nickname": {"type": ["string", "null"]}},
        }
        self.document = {
            "openapi": "3.1.0", "components": {"schemas": {"Person": self.schema}},
            "paths": {"/people": {"post": {
                "operationId": "create_person", "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Person"}}}},
                "responses": {"201": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Person"}}}}},
            }}},
        }
        self.source = (
            "class Person(Model):\n"
            "    __schema_pointer__: ClassVar[str] = '#/components/schemas/Person'\n"
            "    __json_fields__: ClassVar[dict[str,str]] = {'id':'id','nickname':'nickname'}\n"
            "    id: str\n"
            "    nickname: str | None | Unset = UNSET\n"
            "class CreatePersonResponse(ApiResult):\n"
            "    data: Person\n"
        )
        self.source += "SCHEMAS = " + repr({
            "#/components/schemas/Person": self.schema,
            "#/components/schemas/Person/properties/id": self.schema["properties"]["id"],
            "#/components/schemas/Person/properties/nickname": self.schema["properties"]["nickname"],
        }) + "\n"
        self.file = self.root / "sdks/python/src/authgebra/_generated/models.py"
        self.file.write_text(self.source)
        (self.root / AUDIT.OPERATION_FILES["python"]).write_text(
            "def create_person(*, body: models.Person) -> models.CreatePersonResponse:\n    return request('POST', '/people')\n"
        )
        (self.root / "sdks/python/src/authgebra/_generated/aio.py").write_text(
            "async def create_person(*, body: models.Person) -> models.CreatePersonResponse:\n    return request('POST', '/people')\n"
        )
        self.manifest = {"schemas": [{
            "pointer": "#/components/schemas/Person", "type": "Person",
            "file": "sdks/python/src/authgebra/_generated/models.py",
            "fields": [{"pointer": "#/components/schemas/Person/properties/" + name, "name": name}
                       for name in ("id", "nickname")],
        }]}
        self.write_inputs()

    def write_inputs(self):
        (self.root / "sdks/contracts/openapi.json").write_text(json.dumps(self.document))
        (self.root / AUDIT.MANIFESTS["python"]).write_text(json.dumps(self.manifest))

    def test_inventory_and_source_are_independent(self):
        report = AUDIT.build(self.root, ["python"])
        self.assertEqual(report["counts"]["operations"], 1)
        self.assertEqual(report["counts"]["fields"], 2)
        self.assertEqual(report["operations"][0]["request"]["ref"], "#/components/schemas/Person")

    def test_nullable_uses_json_schema_intersection_and_exact_oneof(self):
        self.assertTrue(AUDIT.nullable(self.document, {}))
        self.assertFalse(AUDIT.nullable(self.document, {"oneOf": [{}, {"type": "null"}]}))
        self.assertFalse(AUDIT.nullable(self.document, {"type": ["string", "null"], "enum": ["known"]}))
        self.assertTrue(AUDIT.nullable(self.document, {"anyOf": [{"type": "string"}, {"type": "null"}]}))

    def test_assertions_are_not_arbitrary_json_exceptions(self):
        for schema in ({"minLength": 1}, {"dependentRequired": {"one": ["two"]}},
                       {"not": {"type": "string"}}, {"unevaluatedProperties": False},
                       {"contains": {"type": "string"}}, {"format": "uuid"},
                       {"type": "object", "additionalProperties": {}, "dependentRequired": {"one": ["two"]}}):
            with self.subTest(schema=schema):
                self.assertFalse(AUDIT.unconstrained(self.document, schema))
                self.assertFalse(AUDIT.permits_dynamic(self.document, schema))
        self.assertTrue(AUDIT.unconstrained(self.document, {"description": "arbitrary value", "$comment": "annotation"}))
        self.assertTrue(AUDIT.permits_dynamic(self.document, {"type": "object", "additionalProperties": {}}))

    def test_unsupported_assertions_fail_before_language_claims(self):
        for schema in ({"minLength": 1}, {"type": "object", "dependentRequired": {"one": ["two"]}},
                       {"type": "string", "not": {"enum": ["bad"]}}):
            with self.subTest(schema=schema):
                self.schema["properties"]["id"] = schema
                self.write_inputs()
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    AUDIT.inventory(self.document)

    def test_supported_pattern_and_unique_items_remain_machine_constraints(self):
        self.schema["properties"]["id"]["pattern"] = "^[a-z][a-z0-9_.-]{0,63}$"
        self.schema["properties"]["ids"] = {"type": "array", "items": {"type": "string"}, "maxItems": 512, "uniqueItems": True}
        _schemas, fields, _operations = AUDIT.inventory(self.document)
        self.assertEqual(fields["#/components/schemas/Person/properties/id"]["constraints"]["pattern"], "^[a-z][a-z0-9_.-]{0,63}$")
        self.assertTrue(fields["#/components/schemas/Person/properties/ids"]["constraints"]["uniqueItems"])
        self.assertEqual(fields["#/components/schemas/Person/properties/ids"]["constraints"]["maxItems"], 512)
        self.assertFalse(AUDIT.unconstrained(self.document, self.schema["properties"]["ids"]))

    def test_normalized_value_metadata_is_retained_without_changing_raw_constraints(self):
        metadata = {"operations": ["unicode-trim", "unicode-lowercase"], "schema": {"minLength": 3, "maxLength": 32}, "source": "src/domain/model/identifier.rs:Username::parse"}
        self.schema["properties"]["id"]["x-authgebra-normalized-value"] = metadata
        _schemas, fields, _operations = AUDIT.inventory(self.document)
        self.assertEqual(fields["#/components/schemas/Person/properties/id"]["constraints"]["x-authgebra-normalized-value"], metadata)
        self.assertEqual(fields["#/components/schemas/Person/properties/id"]["constraints"]["minLength"], 1)

    def test_deleted_actual_field_cannot_be_hidden_by_manifest(self):
        self.file.write_text(self.source.replace("    id: str\n", ""))
        with self.assertRaisesRegex(ValueError, "missing declared Python field"):
            AUDIT.build(self.root, ["python"])

    def test_changed_json_name_cannot_be_hidden_by_manifest(self):
        self.file.write_text(self.source.replace("'id':'id'", "'renamed':'id'"))
        with self.assertRaisesRegex(ValueError, "no actual field declaration"):
            AUDIT.build(self.root, ["python"])

    def test_changed_nullable_cannot_be_hidden_by_manifest(self):
        self.file.write_text(self.source.replace("str | None | Unset", "str | Unset"))
        with self.assertRaisesRegex(ValueError, "nullable type differs"):
            AUDIT.build(self.root, ["python"])

    def test_changed_required_cannot_be_hidden_by_manifest(self):
        self.file.write_text(self.source.replace("id: str\n", "id: str | Unset = UNSET\n"))
        with self.assertRaisesRegex(ValueError, "required/absent type differs"):
            AUDIT.build(self.root, ["python"])

    def test_known_field_downgrade_cannot_be_declared_covered(self):
        self.file.write_text(self.source.replace("id: str\n", "id: Any\n"))
        with self.assertRaisesRegex(ValueError, "known field downgraded"):
            AUDIT.build(self.root, ["python"])

    def test_unresolved_reference_fails_before_coverage(self):
        self.schema["properties"]["id"] = {"$ref": "#/components/schemas/Absent"}
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "unresolved JSON pointer"):
            AUDIT.build(self.root, ["python"])

    def test_fake_dynamic_exception_cannot_hide_known_field(self):
        self.manifest["dynamicExceptions"] = [{
            "pointer": "#/components/schemas/Person/properties/id", "reason": "pretend open", "test": "test_it",
        }]
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "dynamic exception masks a known schema"):
            AUDIT.build(self.root, ["python"])

    def test_runtime_constraint_cannot_be_hidden_by_manifest(self):
        self.file.write_text(self.source.replace("'minLength': 1", "'minLength': 2"))
        with self.assertRaisesRegex(ValueError, "actual runtime constraint differs"):
            AUDIT.build(self.root, ["python"])

    def test_operation_request_type_is_checked_in_actual_signature(self):
        operation_file = self.root / AUDIT.OPERATION_FILES["python"]
        operation_file.write_text(operation_file.read_text().replace("body: models.Person", "body: models.WrongRequest"))
        with self.assertRaisesRegex(ValueError, "asynchronous parameter signature differs|actual operation request type differs"):
            AUDIT.build(self.root, ["python"])

    def test_operation_response_payload_is_checked_in_actual_model(self):
        self.file.write_text(self.source.replace("data: Person", "data: JSONValue"))
        with self.assertRaisesRegex(ValueError, "actual operation response type differs"):
            AUDIT.build(self.root, ["python"])

    def test_operation_wire_method_and_path_are_checked(self):
        operation_file = self.root / AUDIT.OPERATION_FILES["python"]
        operation_file.write_text(operation_file.read_text().replace("'/people'", "'/wrong-path'"))
        with self.assertRaisesRegex(ValueError, "HTTP method/path mapping differs"):
            AUDIT.build(self.root, ["python"])

    def test_check_is_byte_exact_and_does_not_rewrite(self):
        output = self.root / "sdks/contracts/type-coverage.json"
        args = ["--root", str(self.root), "--languages", "python"]
        self.assertEqual(AUDIT.main(args), 0)
        self.assertEqual(AUDIT.main(args + ["--check"]), 0)
        output.write_bytes(output.read_bytes().replace(b"\n", b"\r\n"))
        damaged = output.read_bytes()
        self.assertEqual(AUDIT.main(args + ["--check"]), 1)
        self.assertEqual(output.read_bytes(), damaged)


if __name__ == "__main__":
    unittest.main()
