"""Generated models preserve schema fields and JSON absence/null semantics."""

from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path
from typing import Any, cast
import subprocess
import sys

import pytest
import httpx

from authgebra import AsyncClient, Client, Config, JSONValue, ProtocolError, UNSET, api_models
from authgebra._model import Model, decode, json_value, request_body
from .conftest import FakeServer, Script, user_json


def test_generator_emits_named_models() -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "generate.py"
    spec = importlib.util.spec_from_file_location("sdk_typed_generate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    outputs = module.build()
    assert "models.py" in outputs, "admin requests and responses need generated named models"
    assert "class CreateUserRequest" in outputs["models.py"]
    assert "class CreateUserResponse" in outputs["models.py"]
    assert "body: Any" not in outputs["sync.py"]


def test_generation_check_compares_bytes_without_rewriting(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "generate.py"
    spec = importlib.util.spec_from_file_location("sdk_typed_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, content in module.build().items():
        (tmp_path / name).write_bytes(content.encode())
    changed = tmp_path / "models.py"
    changed.write_bytes(changed.read_bytes().replace(b"\n", b"\r\n"))
    before = changed.read_bytes()
    check = subprocess.run([
        sys.executable, "-c",
        "import importlib.util, pathlib, sys; "
        "s=importlib.util.spec_from_file_location('g',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "m.OUT=pathlib.Path(sys.argv[2]); sys.argv=['g','--check']; raise SystemExit(m.main())",
        str(path), str(tmp_path),
    ], capture_output=True, text=True)
    assert check.returncode == 1 and "models.py" in check.stderr
    assert changed.read_bytes() == before


def test_named_models_preserve_absent_null_and_zero() -> None:
    assert api_models.PatchUserRequest().to_json() == {}
    assert api_models.PatchUserRequest(email=None).to_json() == {"email": None}
    assert api_models.PatchUserRequest(email="new@example.test").to_json() == {"email": "new@example.test"}
    assert api_models.PatchPolicyBindingRequest(enabled=False).to_json() == {"enabled": False}
    value = api_models.PatchPolicyBindingRequest(priority=0)
    assert value.to_json() == {"priority": 0}
    assert api_models.ReplaceUserRolesRequest(role_ids=[]).to_json() == {"role_ids": []}
    assert api_models.PatchUserRequest.from_json({}).email is UNSET
    assert api_models.PatchUserRequest.from_json({"email": None}).email is None
    with pytest.raises(ValueError, match="UNSET"):
        json_value([UNSET])


def test_named_nested_union_and_precision() -> None:
    rule = api_models.RewriteRuleDtoInclude(kind=api_models.RewriteRuleDtoIncludeKind.INCLUDE, relation="member")
    request = api_models.CreateRelationDefinitionRequest(
        object_type="document", relation="viewer", subject_types=["user"], rewrites=[rule],
    )
    raw = request_body(request)
    assert isinstance(raw, dict) and raw["rewrites"] == [{"kind": "include", "relation": "member"}]
    parsed = api_models.CreateRelationDefinitionRequest.from_json(raw)
    assert isinstance(parsed.rewrites, list) and isinstance(parsed.rewrites[0], api_models.RewriteRuleDtoInclude)
    large = api_models.TypedValueDtoI64(type=api_models.TypedValueDtoI64Type.I64, value=2**63 - 1)
    assert json.loads(json.dumps(large.to_json()))["value"] == 2**63 - 1
    with pytest.raises(ValueError, match="oneOf"):
        decode("#/components/schemas/RewriteRuleDto", {"kind": "unsupported"})


def test_generated_request_repr_does_not_expose_passwords() -> None:
    request = api_models.CreateUserRequest(username="example", display_name="Example", initial_password="secret-not-for-repr")
    assert "secret-not-for-repr" not in repr(request)


def test_request_from_json_rejects_unknown_fields_before_they_can_be_lost() -> None:
    value = {"username": "example", "display_name": "Example", "initial_password": "example-password-123!", "unknown": True}
    with pytest.raises(ValueError, match="unknown field"):
        api_models.CreateUserRequest.from_json(value)
    with pytest.raises(ValueError, match="CheckRequest/properties/resource"):
        api_models.CheckRequest.from_json({
            "subject_id": "550e8400-e29b-41d4-a716-446655440000", "permission": "document:read",
            "resource": {"resource_type_code": "document", "external_id": "doc-1", "unknown": True},
        })
    # The same nested DTO is also a response value and remains forward compatible there.
    resource = api_models.AuthzResourceRef.from_json({"resource_type_code": "document", "external_id": "doc-1", "future": True})
    assert resource.external_id == "doc-1"
    assert api_models.UserDto.from_json({**user_json(), "future": True}).id == user_json()["id"]


@pytest.mark.parametrize("schema", [
    {"minLength": 1},
    {"type": "string", "not": {"enum": ["bad"]}},
    {"type": "object", "properties": {}, "dependentRequired": {"one": ["two"]}},
    {"type": "object", "properties": {}, "unevaluatedProperties": False},
    {"type": "object", "additionalProperties": {}, "propertyNames": {"pattern": "^[a-z]+$"}},
])
def test_generator_does_not_discard_unsupported_schema_assertions(schema: dict[str, Any]) -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "generate.py"
    spec = importlib.util.spec_from_file_location("sdk_schema_assertions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="unsupported"):
        generator = module.Models({"components": {"schemas": {"Probe": schema}}, "paths": {}})
        generator.render([])


def test_reviewed_batch_and_page_boundaries_are_enforced() -> None:
    subject = "550e8400-e29b-41d4-a716-446655440000"
    resource = api_models.AuthzResourceRef(resource_type_code="document", external_id="doc-1")
    for count in (0, 10001):
        request = api_models.BatchCheckRequest(subject_id=subject, permission="document:read", resources=[resource] * count)
        with pytest.raises(ValueError, match="array too"):
            request_body(request)
    for count in (1, 10000):
        request = api_models.BatchCheckRequest(subject_id=subject, permission="document:read", resources=[resource] * count)
        assert isinstance(request_body(request), dict)
    for limit in (0, 101):
        page = api_models.ListResourcesRequest(subject_id=subject, permission="document:read", resource_type="document", limit=limit)
        with pytest.raises(ValueError, match="number"):
            request_body(page)
    for limit in (1, 100):
        page = api_models.ListResourcesRequest(subject_id=subject, permission="document:read", resource_type="document", limit=limit)
        assert isinstance(request_body(page), dict)
    with pytest.raises(ValueError, match="integer required"):
        api_models.PatchPolicyBindingRequest.from_json({"priority": False})


@pytest.mark.parametrize("asynchronous", [False, True])
def test_named_request_and_response_through_transport(server: FakeServer, asynchronous: bool) -> None:
    server.script = [Script(201, body=json.dumps({"data": {**user_json(), "future_field": True}}).encode())]
    body = api_models.CreateUserRequest(username="example-user", display_name="Example", initial_password="example-password-123!")

    async def execute() -> api_models.CreateUserResponse:
        async with AsyncClient(Config(base_url=server.url)) as client:
            return await client.admin.users.create_user(body=body)

    if asynchronous:
        result = asyncio.run(execute())
    else:
        with Client(Config(base_url=server.url)) as client:
            result = client.admin.users.create_user(body=body)
    assert isinstance(result, api_models.CreateUserResponse)
    assert isinstance(result.data, api_models.UserDto)
    assert result.data.id == user_json()["id"]
    assert result.data["id"] == user_json()["id"]
    sent = json.loads(server.requests[0].body)
    assert sent == body.to_json() and "email" not in sent


def test_missing_required_response_is_protocol_error(server: FakeServer) -> None:
    server.script = [Script(201, body=b'{"data":{"id":"incomplete"}}')]
    with Client(Config(base_url=server.url)) as client, pytest.raises(ProtocolError) as caught:
        client.admin.users.create_user(body={"username": "old-mapping-client"})
    assert caught.value.error == "malformed_success_body"


def test_open_json_is_not_restricted_to_objects() -> None:
    shared = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "request-semantics.json").read_text())
    for value in shared["patch_values"] + shared["integer_values"] + ["text"]:
        assigned = api_models.AttributeAssignmentDto(attribute_id="550e8400-e29b-41d4-a716-446655440000", value=cast(JSONValue, value))
        assert assigned.to_json()["value"] == value
        assert json.loads(json.dumps(assigned.to_json()))["value"] == value


@pytest.mark.parametrize("pointer", [
    row["pointer"] for row in json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "authgebra" / "_generated" / "provenance.json").read_text()
    )["dynamicExceptions"]
])
def test_each_open_json_exception_preserves_json_values(pointer: str) -> None:
    shared = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "request-semantics.json").read_text())
    for value in shared["patch_values"] + shared["integer_values"] + ["text"]:
        assert decode(pointer, value, request=True) == value


@pytest.mark.parametrize("automatic", [False, True])
def test_shared_request_id_vectors(automatic: bool) -> None:
    shared = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "request-semantics.json").read_text())
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"status": "live"})

    with Client(Config(base_url="http://example.invalid", transport=httpx.MockTransport(handler), auto_request_id=automatic)) as client:
        for vector in shared["request_ids"]:
            client.admin.health.live(request_id=vector["value"])
            actual = sent[-1].headers.get("X-Request-ID")
            if vector["valid"]:
                assert actual == vector["value"]
            elif automatic:
                from authgebra._core import valid_request_id
                assert actual is not None and valid_request_id(actual) == actual and actual != vector["value"]
            else:
                assert actual is None


def test_each_named_object_can_decode_and_serialize_its_schema() -> None:
    # Independent input construction reads the reviewed schema, never generated field declarations.
    spec = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "openapi.json").read_text())

    def sample(schema: dict[str, Any]) -> object:
        if "$ref" in schema:
            return sample(spec["components"]["schemas"][schema["$ref"].split("/")[-1]])
        for key in ("oneOf", "anyOf", "allOf"):
            if key in schema:
                return sample(next(arm for arm in schema[key] if arm.get("type") != "null"))
        if "enum" in schema:
            return schema["enum"][0]
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next(k for k in kind if k != "null")
        if kind == "object":
            return {key: sample(value) for key, value in schema.get("properties", {}).items()}
        if kind == "array":
            return [sample(schema["items"]) for _ in range(max(schema.get("minItems", 0), 1))]
        if kind == "string":
            return "550e8400-e29b-41d4-a716-446655440000" if schema.get("format") == "uuid" else "example"
        if kind in ("integer", "number"):
            return max(schema.get("minimum", 0), 0)
        if kind == "boolean":
            return False
        return None

    from authgebra._generated.models import MODEL_TYPES, SCHEMAS
    expected: dict[str, dict[str, Any]] = {}

    def collect(pointer: str, schema: dict[str, Any]) -> None:
        expected[pointer] = schema
        for name, child in schema.get("properties", {}).items():
            collect(pointer + "/properties/" + name.replace("~", "~0").replace("/", "~1"), child)
        for key in ("items", "additionalProperties"):
            if isinstance(schema.get(key), dict):
                collect(pointer + "/" + key, schema[key])
        for key in ("oneOf", "anyOf", "allOf"):
            for index, child in enumerate(schema.get(key, [])):
                collect(f"{pointer}/{key}/{index}", child)

    for name, component in spec["components"]["schemas"].items():
        collect("#/components/schemas/" + name, component)
    assert set(expected) == {pointer for pointer in SCHEMAS if pointer.startswith("#/components/schemas/")}
    for pointer, node_schema in expected.items():
        raw = sample(node_schema)
        decoded = decode(pointer, raw)
        if pointer in MODEL_TYPES:
            assert isinstance(decoded, Model) and isinstance(decoded, MODEL_TYPES[pointer])
        assert json_value(decoded) == raw, pointer


@pytest.mark.parametrize("code", ["a", "role_1", "role.name-v2", "a" * 64])
def test_iam_code_accepts_server_ascii_format(code: str) -> None:
    assert request_body(api_models.CreateRoleRequest(code=code, name="Role")) == {"code": code, "name": "Role"}


@pytest.mark.parametrize("code", ["", "Role", "1role", "role/name", "role:name", "role\n", "a" * 65])
def test_iam_code_rejects_invalid_ascii_format(code: str) -> None:
    with pytest.raises(ValueError):
        request_body(api_models.CreateRoleRequest(code=code, name="Role"))


@pytest.mark.parametrize("length", [1, 64])
def test_iam_name_accepts_server_character_bounds(length: int) -> None:
    name = "名" * length
    assert request_body(api_models.CreateRoleRequest(code="role", name=name)) == {"code": "role", "name": name}
    user = api_models.CreateUserRequest(username="user", display_name=name, initial_password="valid-password")
    encoded = request_body(user)
    assert isinstance(encoded, dict) and encoded["display_name"] == name


@pytest.mark.parametrize("length", [0, 65])
def test_iam_name_rejects_outside_server_character_bounds(length: int) -> None:
    with pytest.raises(ValueError):
        request_body(api_models.CreateRoleRequest(code="role", name="名" * length))
    with pytest.raises(ValueError):
        request_body(api_models.CreateUserRequest(username="user", display_name="名" * length, initial_password="valid-password"))


@pytest.mark.parametrize("field", ["role_ids", "permission_ids"])
def test_exact_set_server_size_and_duplicate_constraints(field: str) -> None:
    from uuid import UUID
    values = [str(UUID(int=index + 1)) for index in range(513)]

    def body(items: list[str]) -> Model:
        if field == "role_ids":
            return api_models.ReplaceUserRolesRequest(role_ids=items)
        return api_models.ReplaceRolePermissionsRequest(permission_ids=items)

    assert request_body(body([])) == {field: []}
    assert request_body(body(values[:512])) == {field: values[:512]}
    with pytest.raises(ValueError, match="long"):
        request_body(body(values))
    with pytest.raises(ValueError, match="duplicate"):
        request_body(body([values[0], values[0]]))
