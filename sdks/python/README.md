# authgebra (Python)

Official Python client for the Authgebra `/api/v1` HTTP API. It is a **thin wrapper**: it calls
the server and interprets the response. It does not evaluate RBAC/ABAC/ReBAC.

| | |
|---|---|
| Distribution / import | `authgebra` |
| Python | `>= 3.10` (tested on 3.10.22 and 3.14.7) |
| Runtime dependency | `httpx` |
| Server API | `/api/v1` |
| Contract digest | OpenAPI export `sdks/contracts/openapi.json`, sha256 `907bd9616dba9993c479cf551bc1e69ac046776b72dd58f5af1138be10c34c25` |
| Verification | **INTEGRATION_TESTED** — [evidence and limits](../DISTRIBUTION.md); native and real integration tests passed |
| Package status | 0.1.0 wheel and sdist on GitHub Releases; PyPI publication is tracked separately |

```bash
python -m pip install ./authgebra-0.1.0-py3-none-any.whl
```

## Quick start

```python
from authgebra import Client, Config, CheckRequest, ResourceRef

config = Config(base_url="https://authgebra.example", token_provider=lambda: access_token)
with Client(config) as client:
    result = client.authorization.check(
        CheckRequest(subject_id, "document:read", resource=ResourceRef("document", "doc-123"))
    )
    result.allowed            # False is a normal result, not an exception
    result.meta               # Meta(authz_revision, snapshot_generation) or None
    result.info.request_id, result.info.trace_id
```

Async: `AsyncClient` has the same surface (`async with`, `await`, `async for`).

`client.admin.<group>.<operation_id>(...)` covers every operation in the OpenAPI export, grouped by
tag (`admin.users.list_users`, `admin.policies.publish_policy`, `admin.relation_tuples.create_relation_tuple`).
Admin calls return a named response with typed `data` and the existing `meta`, `page`, `info`,
`etag`, `replayed`, and `authz_revision_header` members. `from authgebra import api_models` exposes
generated request, response, nested object, enum, and union models. Both clients share these models.
Legacy request mappings and response mapping access remain supported. A query filter that collides with an SDK keyword gets a `query_` prefix
(`query_request_id` for the audit filter).

```python
from authgebra import api_models

request = api_models.CreateRoleRequest(code="reader", name="Reader")
created = client.admin.roles.create_role(body=request)
created.data.id
```

Optional generated fields default to `UNSET` (absent); assigning `None` sends explicit JSON null,
only where the schema allows it. Zero, `False`, and empty collections remain values. Models validate
the reviewed machine constraints before sending; legacy mappings retain server-side validation.
Response decoding requires mandatory fields, preserves integer precision and accepts new object fields.
Source comments and `_generated/provenance.json` identify each schema and field's JSON Pointer.
Only genuinely open JSON schemas use the recursive `JSONValue` type.

## Authentication and transport

`token_provider` is called before every attempt (so refreshed tokens are used); async clients may pass an
`async def`. Tokens never appear in logs, `repr`, or exceptions. TLS verification is always on
(`verify=` takes `True`, a CA bundle path, or an `ssl.SSLContext`; `False` is rejected). Timeouts are
finite by default (connect 5 s, total 30 s per attempt); responses are capped at 8 MiB.
Inject `http_client=` or `transport=` to customise proxies, mTLS, etc. (the SDK does not read proxy
environment variables itself; an injected `httpx` client is never closed by the SDK).

## Errors

| Class | Meaning |
|---|---|
| `ApiError` | The server sent `{"error": {...}}`. Fields: `status`, `code` (raw string), `known_code`, `request_id`, `trace_id`, `violations`, `retryable` (True/False/None), `retry_after_seconds`, `commit_state`, `request_id_mismatch`. |
| `CursorStaleError`, `NotReadyError`, `RateLimitedError`, `ReloadPendingError` | `ApiError` subclasses for the codes callers most often branch on. |
| `TransportError` | No usable response: `error` is `timeout`, `connect_error`, `tls_error`, `connection_reset`, `transport`. |
| `ProtocolError` | A response that violates the contract: `non_json_response`, `malformed_error_body`, `malformed_success_body`, `response_too_large`, `unknown_decision`. |
| `PaginationError` | The page iterator refused to go on. |

Rules: branch on `code`/class, never on `message`. An unknown `code` is kept verbatim with
`known_code=False`. **`NOT_READY` is never turned into a deny**; an unrecognised `decision` is a
`ProtocolError`, not an allow.

## Writes: idempotency and "committed but not published"

* Writes are **never resent automatically**. To allow bounded resends set
  `RetryPolicy(retry_writes=True)` **and** pass the same `idempotency_key=` (`new_idempotency_key()`) with the same body.
* `AUTHZ_RELOAD_PENDING` / `IDENTITY_RELOAD_PENDING` raise `ReloadPendingError` with
  `commit_state=CommitState.COMMITTED_UNPUBLISHED`: the mutation **executed**; it is never resent,
  and you must not report it as "in effect" before publication is confirmed (for example by reading the
  entity or `authorization.revision()`).
* A write that times out or loses its connection raises `TransportError` with `commit_state=UNKNOWN`
  (never assume "not committed"). Resend only with the same idempotency key and body.
* Reads (GET, plus the pure decision POSTs) retry at most `max_retries` (default 2) on `RATE_LIMITED`,
  `NOT_READY`, timeouts and connection failures; `Retry-After` is honoured but capped by `max_delay`.

## Tracing

Every request carries a valid `X-Request-ID` (`py-<hex>` unless you pass `request_id=`) and a W3C
`traceparent` (yours via `traceparent=` or `Config.trace_provider`, else a new trace; each retry is a new span of the same
trace). Results and errors expose `request_id` (response header preferred, body as fallback) and `trace_id`
(`error.trace_id`, else `X-Trace-ID`/`traceparent` response headers; invalid values are ignored).

## Pagination

`list_resources(request)` returns one page; `iter_list_resources(request, max_pages=100)` yields pages lazily.
`CURSOR_STALE` is raised as `CursorStaleError` (the SDK never restarts silently), a snapshot-generation change
between pages raises `PaginationError`, and the iterator is bounded by `max_pages`.

## Versions and numbers

`authz_revision` / `snapshot_generation` are Python `int`s parsed from the raw JSON text (no float round trip).
Admin reads have no `meta`; the SDK reports `None`, never `0`.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python tools/generate.py --check   # generated admin surface matches ../contracts/openapi.json
.venv/bin/python -m pytest                   # runs all shared fixtures (sync + async) and behavior tests
.venv/bin/mypy --strict src tests tools examples
```

Licensing: proprietary, same terms as the Authgebra repository (`LICENSE`).
