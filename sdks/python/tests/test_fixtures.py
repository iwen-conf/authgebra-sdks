"""Run every shared golden fixture (``sdks/fixtures/cases/*.json``) against the SDK.

Each case runs twice: with the sync :class:`Client` and with the :class:`AsyncClient`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest

from authgebra import (
    ApiError,
    AsyncClient,
    CheckRequest,
    Client,
    Config,
    ListResourcesRequest,
    ProtocolError,
    RetryPolicy,
    TransportError,
)

from .conftest import FakeServer, Script

CASES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "cases"
CASE_FILES = sorted(CASES_DIR.glob("*.json"))
SUBJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
TOKEN = "tok-SECRET-do-not-leak"


def _plan(case: dict[str, Any]) -> Callable[[Any], Any]:
    request = case.get("request") or {}
    if request.get("method"):
        return lambda c: c.request(
            request["method"],
            request["path"],
            json=request.get("body"),
            idempotency_key=request.get("idempotency_key"),
        )
    table: dict[str, Callable[[Any], Any]] = {
        "authz.check": lambda c: c.authorization.check(CheckRequest(SUBJECT, "document:read")),
        "authz.list_resources": lambda c: c.authorization.list_resources(
            ListResourcesRequest(SUBJECT, "document:read", "document")
        ),
        "users.update": lambda c: c.admin.users.patch_user(
            str(SUBJECT), body={"display_name": "x"}, if_match='"1"'
        ),
        "auth.change_password": lambda c: c.admin.auth.change_password(
            body={"current_password": "a", "new_password": "b"}
        ),
        "users.list": lambda c: c.admin.users.list_users(),
    }
    return table[case["operation"]]


def _script(case: dict[str, Any]) -> Script:
    if "fault" in case:
        return Script(fault=case["fault"])
    response = case["response"]
    if "body_raw" in response:
        body = response["body_raw"].encode("utf-8")
    elif "body" in response:
        body = json.dumps(response["body"], ensure_ascii=False).encode("utf-8")
    else:
        body = b""
    return Script(status=response["status"], headers=dict(response.get("headers", {})), body=body)


def _outcome(mode: str, cfg: Config, fn: Callable[[Any], Any]) -> Any:
    if mode == "sync":
        with Client(cfg) as client:
            try:
                return fn(client)
            except Exception as exc:  # the exception object *is* the outcome under test
                return exc

    async def run() -> Any:
        async with AsyncClient(cfg) as client:
            try:
                return await fn(client)
            except Exception as exc:
                return exc

    return asyncio.run(run())


def _assert_expect(case: dict[str, Any], out: Any, server: FakeServer) -> None:
    expect = case["expect"]
    kind = expect["kind"]
    if kind == "ok":
        assert not isinstance(out, Exception), repr(out)
        meta = out.meta
        if "decision" in expect:
            assert out.decision.value == expect["decision"]
        if "authz_revision" in expect:
            if expect["authz_revision"] is None:
                assert meta is None
            else:
                assert meta is not None and str(meta.authz_revision) == str(expect["authz_revision"])
        if "snapshot_generation" in expect:
            assert meta is not None and str(meta.snapshot_generation) == str(expect["snapshot_generation"])
        if "trace_id" in expect:
            assert out.info.trace_id == expect["trace_id"]
        if "request_id" in expect:
            assert out.info.request_id == expect["request_id"]
        if "has_more" in expect:
            assert out.page.has_more is expect["has_more"]
        if "echo_username" in expect:
            assert out.data["username"] == expect["echo_username"]
        if expect.get("request_body_utf8_exact"):
            sent = server.requests[0].body
            assert expect["echo_username"].encode("utf-8") in sent, "non-ASCII must be sent as raw UTF-8"
            assert b"\\u" not in sent
        return
    assert isinstance(out, Exception), "expected an error outcome"
    if kind == "api_error":
        assert isinstance(out, ApiError), repr(out)
        assert out.status == expect["status"]
        assert out.code == expect["code"]
        assert out.known_code is expect["known_code"]
        assert out.request_id == expect["request_id"]
        assert out.trace_id == expect["trace_id"]
        assert out.retryable == expect["retryable"]
        assert out.retry_after_seconds == expect["retry_after_seconds"]
        want_state = expect["commit_state"]
        assert (out.commit_state.value if out.commit_state else None) == want_state
        if "violations" in expect:
            assert [(v.path, v.reason) for v in out.violations] == [
                (v["path"], v["reason"]) for v in expect["violations"]
            ]
        if "body_request_id" in expect:
            assert out.body_request_id == expect["body_request_id"]
        if "request_id_mismatch" in expect:
            assert out.request_id_mismatch is expect["request_id_mismatch"]
        if "request_id_header_valid" in expect:
            assert out.request_id_header_valid is expect["request_id_header_valid"]
    elif kind == "transport_error":
        assert isinstance(out, TransportError), repr(out)
        assert out.error == expect["error"]
        assert (out.commit_state.value if out.commit_state else None) == expect["commit_state"]
        if expect.get("auto_resent") is False:
            assert len(server.requests) == 1, "an unsafe write must not be resent"
    elif kind == "protocol_error":
        assert isinstance(out, ProtocolError), repr(out)
        assert out.error == expect["error"]
        assert out.status == expect["status"]
    else:  # pragma: no cover
        raise AssertionError(f"unknown expect.kind {kind}")
    assert TOKEN not in str(out) and TOKEN not in repr(out), "token leaked into an error"


def test_fixture_directory_is_populated() -> None:
    assert len(CASE_FILES) == 25


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("path", CASE_FILES, ids=[p.stem for p in CASE_FILES])
def test_case(path: Path, mode: str, server: FakeServer) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    server.script = [_script(case)]
    cfg = Config(
        base_url=server.url,
        token_provider=lambda: TOKEN,
        timeout=0.4 if "fault" in case else 5.0,
        connect_timeout=1.0,
        retry=RetryPolicy(max_retries=2, base_delay=0.001, max_delay=0.01),
    )
    out = _outcome(mode, cfg, _plan(case))
    _assert_expect(case, out, server)
    # every exchange carried the SDK's correlation + auth headers
    sent = server.requests[0]
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["user-agent"].startswith("authgebra-python/")
    assert sent.headers["x-request-id"].startswith("py-")
    assert sent.headers["traceparent"].startswith("00-")
