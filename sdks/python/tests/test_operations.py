"""The generated admin surface must cover every operation of the reviewed OpenAPI export."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import authgebra
from authgebra import AsyncClient, Client, Config
from authgebra._generated.operations import OPERATIONS, SPEC_SHA256

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
ROOT = Path(__file__).resolve().parents[1]


def _spec_operation_ids() -> set[str]:
    spec = json.loads((CONTRACTS / "openapi.json").read_text(encoding="utf-8"))
    return {op["operationId"] for item in spec["paths"].values() for op in item.values()}


def test_every_openapi_operation_has_a_sync_and_async_method() -> None:
    ids = _spec_operation_ids()
    assert ids, "reviewed contract must contain operations"
    sync = Client(Config(base_url="http://localhost"))
    aio = AsyncClient(Config(base_url="http://localhost"))
    missing: list[str] = []
    for op_id, group, _method, _path in OPERATIONS:
        for admin in (sync.admin, aio.admin):
            if not callable(getattr(getattr(admin, group, None), op_id, None)):
                missing.append(f"{group}.{op_id}")
    assert not missing, missing
    assert {o[0] for o in OPERATIONS} == ids
    for admin in (sync.admin, aio.admin):
        actual = {
            name for group in vars(admin).values() for name, member in vars(type(group)).items()
            if not name.startswith("_") and callable(member)
        }
        assert actual == ids, "public methods must match the reviewed operation set in both directions"
    sync.close()


def test_generated_code_is_in_sync_with_the_contract_and_digest() -> None:
    digest = (CONTRACTS / "openapi.sha256").read_text().strip()
    assert hashlib.sha256((CONTRACTS / "openapi.json").read_bytes()).hexdigest() == digest
    assert SPEC_SHA256 == digest
    spec = importlib.util.spec_from_file_location("sdk_generate", ROOT / "tools" / "generate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = ROOT / "src" / "authgebra" / "_generated"
    for name, text in module.build().items():
        assert (out / name).read_text() == text, f"{name} is stale; run tools/generate.py"


def test_public_surface() -> None:
    assert authgebra.__version__ == "0.1.0"
    assert authgebra.SPEC_SHA256 == SPEC_SHA256
