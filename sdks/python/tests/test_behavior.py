"""Behavior required by sdks/README.md beyond the golden fixtures."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
import pytest

from authgebra import (
    ApiError,
    AsyncClient,
    BatchCheckRequest,
    CheckRequest,
    Client,
    Config,
    CursorStaleError,
    ExplainRequest,
    ListResourcesRequest,
    NotReadyError,
    PaginationError,
    ProtocolError,
    ReloadPendingError,
    ResourceRef,
    RetryPolicy,
    TransportError,
    new_idempotency_key,
    parse_traceparent,
)

from .conftest import FakeServer, Script

SUBJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
TOKEN = "tok-SECRET-do-not-leak"
FAST = RetryPolicy(max_retries=2, base_delay=0.001, max_delay=0.01)


def ok(data: Any, **extra: Any) -> Script:
    return Script(200, body=json.dumps({"data": data, **extra}).encode())


def err(status: int, code: str, **extra: Any) -> Script:
    body = {"error": {"code": code, "message": "m", "request_id": "req-abcdefgh", **extra}}
    return Script(status, body=json.dumps(body).encode())


CHECK_OK = ok({"decision": "allow", "reason_code": "allow"}, meta={"authz_revision": 5, "snapshot_generation": 5})


def client(server: FakeServer, **overrides: Any) -> Client:
    params: dict[str, Any] = {"retry": FAST, "token_provider": lambda: TOKEN, "timeout": 2.0}
    params.update(overrides)
    return Client(Config(base_url=server.url, **params))


def check(c: Client) -> Any:
    return c.authorization.check(CheckRequest(SUBJECT, "document:read"))


# ----------------------------------------------------------------------------- retries


def test_read_retries_rate_limit_then_succeeds_and_caps_retry_after(server: FakeServer) -> None:
    limited = err(429, "RATE_LIMITED", retry_after_seconds=3600)
    limited.headers["Retry-After"] = "3600"
    server.script = [limited, limited, CHECK_OK]
    with client(server) as c:
        assert check(c).allowed
    assert len(server.requests) == 3  # a huge Retry-After is capped by max_delay (test would hang otherwise)


def test_read_retries_are_bounded(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True)]
    with client(server) as c, pytest.raises(NotReadyError) as info:
        check(c)
    assert len(server.requests) == 3 and info.value.attempts == 3


@pytest.mark.parametrize(
    "status,code",
    [(403, "FORBIDDEN"), (401, "TOKEN_EXPIRED"), (409, "CURSOR_STALE"), (422, "VALIDATION_ERROR"), (500, "INTERNAL_ERROR")],
)
def test_non_retryable_errors_are_sent_once(server: FakeServer, status: int, code: str) -> None:
    server.script = [err(status, code)]
    with client(server) as c, pytest.raises(ApiError):
        check(c)
    assert len(server.requests) == 1


def test_not_ready_is_an_error_never_a_deny(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True)]
    with client(server, retry=RetryPolicy(max_retries=0)) as c:
        with pytest.raises(NotReadyError) as info:
            check(c)
    assert info.value.code == "NOT_READY" and info.value.status == 503


def test_cursor_stale_has_its_own_type(server: FakeServer) -> None:
    server.script = [err(409, "CURSOR_STALE", retryable=False)]
    with client(server) as c, pytest.raises(CursorStaleError):
        c.authorization.list_resources(ListResourcesRequest(SUBJECT, "document:read", "document"))


def test_write_is_not_retried_by_default(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True), ok({"id": "x"})]
    with client(server) as c, pytest.raises(NotReadyError):
        c.request("POST", "/api/v1/users", json={"username": "a"}, idempotency_key="idem-0001")
    assert len(server.requests) == 1


def test_write_without_idempotency_key_is_never_retried_even_if_opted_in(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True), ok({"id": "x"})]
    policy = RetryPolicy(max_retries=2, base_delay=0.001, max_delay=0.01, retry_writes=True)
    with client(server, retry=policy) as c, pytest.raises(NotReadyError):
        c.request("POST", "/api/v1/users", json={"username": "a"})
    assert len(server.requests) == 1


def test_write_with_key_and_opt_in_retries_with_the_same_key_and_payload(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True), ok({"id": "x"})]
    policy = RetryPolicy(max_retries=2, base_delay=0.001, max_delay=0.01, retry_writes=True)
    key = new_idempotency_key()
    with client(server, retry=policy) as c:
        result = c.request("POST", "/api/v1/users", json={"username": "a"}, idempotency_key=key)
    assert result.data == {"id": "x"}
    assert len(server.requests) == 2
    first, second = server.requests
    assert first.headers["idempotency-key"] == second.headers["idempotency-key"] == key
    assert first.body == second.body


@pytest.mark.parametrize("code", ["AUTHZ_RELOAD_PENDING", "IDENTITY_RELOAD_PENDING"])
def test_reload_pending_is_never_resent(server: FakeServer, code: str) -> None:
    server.script = [err(503, code, retryable=False, commit_state="committed_unpublished"), ok({"id": "x"})]
    policy = RetryPolicy(max_retries=5, base_delay=0.001, max_delay=0.01, retry_writes=True)
    with client(server, retry=policy) as c, pytest.raises(ReloadPendingError) as info:
        c.request("POST", "/api/v1/users", json={"username": "a"}, idempotency_key="idem-0001")
    assert len(server.requests) == 1
    assert info.value.commit_state is not None and info.value.commit_state.value == "committed_unpublished"
    assert info.value.retryable is False


def test_reload_pending_without_server_commit_state_is_still_not_resent(server: FakeServer) -> None:
    # even an old server that omits commit_state: the code itself is not in the retryable set
    server.script = [err(503, "AUTHZ_RELOAD_PENDING"), ok({"id": "x"})]
    policy = RetryPolicy(max_retries=5, base_delay=0.001, max_delay=0.01, retry_writes=True)
    with client(server, retry=policy) as c, pytest.raises(ReloadPendingError):
        c.request("POST", "/api/v1/users", json={}, idempotency_key="idem-0001")
    assert len(server.requests) == 1


# ----------------------------------------------------------------------------- pagination


def page(items: list[str], cursor: str | None, generation: int | None = 7) -> Script:
    data = [{"resource_type_code": "document", "external_id": i} for i in items]
    body: dict[str, Any] = {"data": data, "page": {"next_cursor": cursor, "has_more": cursor is not None}}
    if generation is not None:
        body["meta"] = {"authz_revision": 9, "snapshot_generation": generation}
    return Script(200, body=json.dumps(body).encode())


LIST = ListResourcesRequest(SUBJECT, "document:read", "document", limit=2)


def test_iterator_walks_pages_lazily_and_sends_cursors(server: FakeServer) -> None:
    server.script = [page(["a", "b"], "c1"), page(["c"], None)]
    with client(server) as c:
        it = c.authorization.iter_list_resources(LIST)
        assert len(server.requests) == 0, "nothing is fetched before the first next()"
        first = next(it)
        assert [r.external_id for r in first.items] == ["a", "b"] and len(server.requests) == 1
        rest = list(it)
    assert [r.external_id for r in rest[0].items] == ["c"]
    assert "cursor" not in json.loads(server.requests[0].body)
    assert json.loads(server.requests[1].body)["cursor"] == "c1"


def test_iterator_raises_cursor_stale_and_does_not_restart(server: FakeServer) -> None:
    server.script = [page(["a"], "c1"), err(409, "CURSOR_STALE", retryable=False), page(["a"], None)]
    with client(server) as c:
        it = c.authorization.iter_list_resources(LIST)
        next(it)
        with pytest.raises(CursorStaleError):
            next(it)
    assert len(server.requests) == 2  # no silent restart from page one


def test_iterator_refuses_to_stitch_generations(server: FakeServer) -> None:
    server.script = [page(["a"], "c1", generation=7), page(["b"], None, generation=8)]
    with client(server) as c, pytest.raises(PaginationError, match="generation"):
        list(c.authorization.iter_list_resources(LIST))


def test_iterator_is_bounded_and_detects_cursor_loops(server: FakeServer) -> None:
    server.script = [page(["a"], "c1"), page(["b"], "c2"), page(["c"], "c3")]
    with client(server) as c, pytest.raises(PaginationError, match="page limit"):
        list(c.authorization.iter_list_resources(LIST, max_pages=2))
    server.requests.clear()
    server.script = [page(["a"], "same"), page(["b"], "same")]
    with client(server) as c, pytest.raises(PaginationError, match="repeated cursor"):
        list(c.authorization.iter_list_resources(LIST))


def test_async_iterator_matches_sync_semantics(server: FakeServer) -> None:
    server.script = [page(["a"], "c1"), err(409, "CURSOR_STALE", retryable=False)]

    async def run() -> None:
        async with AsyncClient(Config(base_url=server.url, retry=FAST)) as c:
            it = c.authorization.iter_list_resources(LIST)
            first = await it.__anext__()
            assert first.items[0].external_id == "a"
            with pytest.raises(CursorStaleError):
                await it.__anext__()

    asyncio.run(run())


# ----------------------------------------------------------------------------- typed results


def test_unknown_decision_is_fail_closed_protocol_error(server: FakeServer) -> None:
    server.script = [ok({"decision": "maybe", "reason_code": "x"})]
    with client(server) as c, pytest.raises(ProtocolError) as info:
        check(c)
    assert info.value.error == "unknown_decision"


def test_batch_explain_revision_roundtrip(server: FakeServer) -> None:
    ref = {"resource_type_code": "document", "external_id": "d1"}
    server.script = [
        ok({"allowed": [{"resource": ref, "reason_code": "allow"}], "denied": []}),
        ok({"decision": "deny", "reason_code": "missing_permission", "policies": [], "truncated": False, "total_steps": 3}),
        ok({"db_revision": 9223372036854775807, "snapshot_generation": 12, "status": "in_sync"}),
    ]
    with client(server) as c:
        batch = c.authorization.batch_check(
            BatchCheckRequest(SUBJECT, "document:read", [ResourceRef("document", "d1")], context={})
        )
        explain = c.authorization.explain(ExplainRequest(SUBJECT, "document:read"))
        rev = c.authorization.revision()
    assert batch.allowed[0].resource.external_id == "d1" and batch.denied == ()
    assert explain.decision.value == "deny" and explain.total_steps == 3
    assert rev.db_revision == 9223372036854775807 and rev.meta is None
    sent = json.loads(server.requests[0].body)
    assert sent["context"] == {} and sent["resources"] == [ref]


def test_request_models_omit_unset_fields_and_validate_ids() -> None:
    body = CheckRequest(SUBJECT, "p").to_json()
    assert set(body) == {"subject_id", "permission"}  # no null filler, no defaults
    assert CheckRequest(SUBJECT, "p", context=None).to_json().get("context") is None
    assert "context" not in CheckRequest(SUBJECT, "p", context=None).to_json()
    assert CheckRequest(str(SUBJECT).upper(), "p").to_json()["subject_id"] == str(SUBJECT)
    with pytest.raises(ValueError):
        CheckRequest("not-a-uuid", "p").to_json()


def test_bigint_survives_exactly(server: FakeServer) -> None:
    raw = b'{"data":{"decision":"allow","reason_code":"allow"},"meta":{"authz_revision":9223372036854775807,"snapshot_generation":-9223372036854775808}}'
    server.script = [Script(200, body=raw)]
    with client(server) as c:
        result = check(c)
    assert result.meta is not None
    assert result.meta.authz_revision == 2**63 - 1 and result.meta.snapshot_generation == -(2**63)
    assert isinstance(result.meta.authz_revision, int)


# ----------------------------------------------------------------------------- tracing / ids


def test_traceparent_and_request_id_are_injected_and_valid(server: FakeServer) -> None:
    server.script = [CHECK_OK]
    with client(server) as c:
        check(c)
    headers = server.requests[0].headers
    assert parse_traceparent(headers["traceparent"]) is not None
    assert headers["x-request-id"].startswith("py-")


def test_explicit_trace_and_request_id_are_used_and_invalid_ones_replaced(server: FakeServer) -> None:
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    server.script = [CHECK_OK, CHECK_OK]
    with client(server) as c:
        c.authorization.check(CheckRequest(SUBJECT, "p"), request_id="my-request-0001", traceparent=tp)
        c.authorization.check(CheckRequest(SUBJECT, "p"), request_id="bad id", traceparent="garbage")
    one, two = (r.headers for r in server.requests)
    assert one["x-request-id"] == "my-request-0001" and one["traceparent"] == tp
    assert two["x-request-id"].startswith("py-") and two["traceparent"] != "garbage"
    assert parse_traceparent(two["traceparent"]) is not None


def test_trace_provider_continues_the_callers_trace_and_retries_get_new_spans(server: FakeServer) -> None:
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    server.script = [err(503, "NOT_READY", retryable=True), CHECK_OK]
    with client(server, trace_provider=lambda: tp) as c:
        check(c)
    first = parse_traceparent(server.requests[0].headers["traceparent"])
    second = parse_traceparent(server.requests[1].headers["traceparent"])
    assert first is not None and second is not None
    assert first[0] == second[0] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert first[1] != second[1], "each attempt is its own span"


def test_idempotency_key_and_if_match_headers_and_validation(server: FakeServer) -> None:
    from .conftest import user_json
    server.script = [ok(user_json())]
    with client(server) as c:
        c.admin.users.patch_user(str(SUBJECT), body={"a": 1}, if_match='"3"', idempotency_key=" idem-key.1 ")
        with pytest.raises(ValueError):
            c.admin.users.create_user(body={}, idempotency_key="has space")
    headers = server.requests[0].headers
    assert headers["if-match"] == '"3"' and headers["idempotency-key"] == "idem-key.1"
    assert len(server.requests) == 1  # invalid key is rejected before anything is sent


# ----------------------------------------------------------------------------- transport / limits


def test_response_size_limit(server: FakeServer) -> None:
    server.script = [Script(200, body=b"x" * 500)]
    with client(server, max_response_bytes=100) as c, pytest.raises(ProtocolError) as info:
        check(c)
    assert info.value.error == "response_too_large"


def test_connect_failure_is_a_transport_error() -> None:
    cfg = Config(base_url="http://127.0.0.1:1", retry=RetryPolicy(max_retries=0), connect_timeout=1.0, timeout=2.0)
    with Client(cfg) as c, pytest.raises(TransportError) as info:
        c.authorization.check(CheckRequest(SUBJECT, "p"))
    assert info.value.error in {"connect_error", "timeout"}
    assert info.value.commit_state is None  # a read: nothing was possibly committed


def test_write_connect_failure_marks_commit_state_unknown() -> None:
    cfg = Config(base_url="http://127.0.0.1:1", retry=RetryPolicy(max_retries=0), connect_timeout=1.0, timeout=2.0)
    with Client(cfg) as c, pytest.raises(TransportError) as info:
        c.request("POST", "/api/v1/users", json={})
    assert info.value.commit_state is not None and info.value.commit_state.value == "unknown"


def test_injected_transport_and_client_ownership() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "live"})

    cfg = Config(base_url="http://example.invalid", transport=httpx.MockTransport(handler))
    with Client(cfg) as c:
        assert c.admin.health.live().data == {"status": "live"}  # bare (non-enveloped) health body

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with Client(Config(base_url="http://example.invalid", http_client=http)):
        pass
    assert not http.is_closed, "an injected httpx client must not be closed by the SDK"
    http.close()


def test_async_cancellation_does_not_retry(server: FakeServer) -> None:
    server.script = [Script(fault="timeout")]

    async def run() -> None:
        cfg = Config(base_url=server.url, timeout=10.0, retry=RetryPolicy(max_retries=5, base_delay=0.001, max_delay=0.01))
        async with AsyncClient(cfg) as c:
            task = asyncio.create_task(c.authorization.check(CheckRequest(SUBJECT, "p")))
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert len(server.requests) == 1


def test_async_token_provider_may_be_a_coroutine(server: FakeServer) -> None:
    server.script = [CHECK_OK]

    async def provider() -> str:
        return "async-token-1"

    async def run() -> None:
        async with AsyncClient(Config(base_url=server.url, token_provider=provider)) as c:
            assert (await c.authorization.check(CheckRequest(SUBJECT, "p"))).allowed

    asyncio.run(run())
    assert server.requests[0].headers["authorization"] == "Bearer async-token-1"


def test_token_is_fetched_for_every_attempt(server: FakeServer) -> None:
    server.script = [err(503, "NOT_READY", retryable=True), CHECK_OK]
    tokens = iter(["t1", "t2"])
    with client(server, token_provider=lambda: next(tokens)) as c:
        check(c)
    assert [r.headers["authorization"] for r in server.requests] == ["Bearer t1", "Bearer t2"]


def test_token_never_reaches_logs_repr_or_errors(server: FakeServer, caplog: pytest.LogCaptureFixture) -> None:
    server.script = [err(403, "FORBIDDEN")]
    cfg = Config(base_url=server.url, token_provider=lambda: TOKEN, headers={"X-Team": "a"}, retry=FAST)
    with caplog.at_level(logging.DEBUG, logger="authgebra"):
        with Client(cfg) as c, pytest.raises(ApiError) as info:
            check(c)
    assert TOKEN not in caplog.text and TOKEN not in repr(cfg) and TOKEN not in str(info.value)
    assert TOKEN not in info.value.raw_body_excerpt


# ----------------------------------------------------------------------------- config / admin


def test_config_rejects_unsafe_settings() -> None:
    with pytest.raises(ValueError, match="TLS"):
        Config(base_url="https://x", verify=False)
    with pytest.raises(ValueError):
        Config(base_url="ftp://x")
    with pytest.raises(ValueError):
        Config(base_url="https://x", timeout=0)
    with pytest.raises(ValueError):
        Config(base_url="https://x", headers={"Authorization": "Bearer evil"})
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=11)


def test_query_and_path_encoding(server: FakeServer) -> None:
    from .conftest import user_json
    server.script = [ok([], page={"next_cursor": None, "has_more": False}),
                     ok([], page={"next_cursor": None, "has_more": False}), ok(user_json())]
    with client(server) as c:
        c.admin.roles.list_roles(limit=5, q="a b&c", enabled=True)
        c.admin.audit.list_audit_events(query_request_id="req-12345678", from_="2026-01-01")
        c.admin.users.get_user("a/b")
    r1, r2, r3 = server.requests
    assert "limit=5" in r1.path and "q=a+b%26c" in r1.path and "enabled=true" in r1.path
    assert "request_id=req-12345678" in r2.path and "from=2026-01-01" in r2.path
    assert r3.path == "/api/v1/users/a%2Fb"


def test_admin_reads_without_meta_expose_none_not_zero(server: FakeServer) -> None:
    server.script = [ok([], page={"next_cursor": None, "has_more": False})]
    with client(server) as c:
        result = c.admin.users.list_users()
    assert result.meta is None and result.page is not None and result.page.has_more is False
