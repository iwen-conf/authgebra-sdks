"""Run the documented write through the SDK and validate its wire payload."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from .conftest import FakeServer, Script, user_json


def test_idempotent_write_example_matches_create_user_contract(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root.parent / "contracts" / "openapi.json").read_text())
    schema = spec["components"]["schemas"]["CreateUserRequest"]
    monkeypatch.setenv("AUTHGEBRA_URL", server.url)
    monkeypatch.setenv("AUTHGEBRA_TOKEN", "example-test-token")
    monkeypatch.setenv("NEW_USER_PASSWORD", "example-test-password-123!")
    server.script = [Script(201, body=json.dumps({"data": user_json()}).encode())]

    runpy.run_path(str(root / "examples" / "idempotent_write.py"), run_name="__main__")

    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.method == "POST" and request.path == "/api/v1/users"
    assert request.headers["idempotency-key"]
    body = json.loads(request.body)
    assert set(schema["required"]) <= body.keys()
    assert body.keys() <= schema["properties"].keys()
    for name in schema["required"]:
        assert isinstance(body[name], str) and body[name]
