"""Contract assertions independent of the SDK generators."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SchemaSemanticsTests(unittest.TestCase):
    def test_existing_runtime_bounds_are_machine_readable(self):
        schemas = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())["components"]["schemas"]
        resources = schemas["BatchCheckRequest"]["properties"]["resources"]
        self.assertEqual((resources.get("minItems"), resources.get("maxItems")), (1, 10000))
        limit = schemas["ListResourcesRequest"]["properties"]["limit"]
        self.assertEqual((limit.get("minimum"), limit.get("maximum")), (1, 100))

    def test_iam_and_fact_constraints_match_validators(self):
        schemas = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())["components"]["schemas"]
        for model, field in (("CreateUserRequest", "display_name"), ("PatchUserRequest", "display_name"),
                             ("CreateRoleRequest", "name"), ("PatchRoleRequest", "name"),
                             ("CreatePermissionRequest", "name"), ("CreateResourceRequest", "name")):
            with self.subTest(model=model, field=field):
                prop = schemas[model]["properties"][field]
                self.assertEqual((prop.get("minLength"), prop.get("maxLength")), (1, 64))
        for model, field in (("CreateUserRequest", "role_ids"), ("ReplaceUserRolesRequest", "role_ids"),
                             ("ReplaceRolePermissionsRequest", "permission_ids")):
            prop = schemas[model]["properties"][field]
            self.assertEqual(prop.get("maxItems"), 512)
            self.assertIs(prop.get("uniqueItems"), True)
        for model, field in (("CreateRoleRequest", "code"), ("CreatePermissionRequest", "action"),
                             ("CreateResourceRequest", "resource_type_code"), ("CreatePolicyRequest", "code")):
            self.assertEqual(schemas[model]["properties"][field].get("pattern"), r"^[a-z][a-z0-9_.-]{0,63}$")

    def test_query_limits_preserve_endpoint_specific_ranges(self):
        document = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())
        for item in document["paths"].values():
            for operation in item.values():
                for parameter in operation.get("parameters", []):
                    if parameter.get("name") == "limit":
                        maximum = 100 if operation["operationId"] in {"list_audit_events", "list_relation_definitions", "list_relation_tuples"} else 200
                        self.assertEqual((parameter["schema"].get("minimum"), parameter["schema"].get("maximum")), (1, maximum))

    def test_normalization_constraints_do_not_restrict_raw_accepted_input(self):
        schemas = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())["components"]["schemas"]
        username = schemas["CreateUserRequest"]["properties"]["username"]
        self.assertNotIn("maxLength", username)
        normalized = username["x-authgebra-normalized-value"]
        self.assertEqual(normalized["operations"], ["unicode-trim", "unicode-lowercase"])
        self.assertEqual((normalized["schema"]["minLength"], normalized["schema"]["maxLength"]), (3, 32))

    def test_delete_responses_use_the_existing_named_ack_shape(self):
        document = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())
        for item in document["paths"].values():
            for operation in item.values():
                if operation["operationId"] in {"delete_attribute", "delete_policy", "delete_policy_binding", "delete_relation_definition", "delete_relation_tuple", "delete_resource", "delete_role"}:
                    self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ApiResponse_MutationAckDto")
        ack = document["components"]["schemas"]["MutationAckDto"]
        self.assertEqual(ack["required"], ["replayed"])
        self.assertEqual(ack["properties"]["replayed"]["type"], "boolean")

    def test_patch_clearability_is_machine_semantics(self):
        schemas = json.loads((ROOT / "sdks/contracts/openapi.json").read_text())["components"]["schemas"]
        for model, field in [("PatchUserRequest", "email"), ("PatchRoleRequest", "description"),
                             ("PatchPolicyRequest", "description"), ("PatchResourceTypeRequest", "description")]:
            with self.subTest(model=model, field=field):
                prop = schemas[model]["properties"][field]
                self.assertIs(prop.get("x-authgebra-clearable"), True)
                self.assertIn("null", prop["type"])
                self.assertNotIn(field, schemas[model].get("required", []))


if __name__ == "__main__":
    unittest.main()
