# authgebra-zig

Official Zig client for the Authgebra `/api/v1` HTTP API. It is a thin, typed wrapper over HTTP:
**it never evaluates RBAC/ABAC/ReBAC itself** and never logs anything.

> Status: **INTEGRATION_TESTED** — native live-server scenarios and publication recovery passed.
> See [evidence and limits](../DISTRIBUTION.md). 0.1.0 is distributed as a source package on GitHub Releases.

## Compatibility matrix

| Item | Value |
|---|---|
| Zig | **0.17.0 exactly** (`minimum_zig_version = "0.17.0"`). `std.Io` / `std.http` changed a lot between releases; no other version is tested or supported. |
| Server API | `/api/v1` |
| OpenAPI contract | `sdks/contracts/openapi.json`, sha256 in `sdks/contracts/openapi.sha256` (`zig build check-generated` fails when the generated admin surface is stale) |
| Platforms tested | macOS (aarch64). Linux/Windows: NOT_RUN. |
| TLS | `std.crypto.tls` via `std.http.Client`; certificate verification is always on — there is no switch to turn it off. |

## Install

```sh
zig fetch --save=authgebra_zig https://github.com/iwen-conf/authgebra-sdks/releases/download/v0.1.0/authgebra-zig-0.1.0.tar.gz
```

```zig
// build.zig
const authgebra = b.dependency("authgebra_zig", .{ .target = target, .optimize = optimize });
exe.root_module.addImport("authgebra", authgebra.module("authgebra"));
```

## Quick start

```zig
const authgebra = @import("authgebra");

pub fn main(init: std.process.Init) !void {
    var client = authgebra.Client.init(init.gpa, init.io, .{
        .base_url = "https://authz.example.com",
        .token_provider = .{ .getFn = myTokenFn },   // returns an allocated slice, SDK zeroes+frees it
        .total_timeout_ms = 10_000,
    });
    defer client.deinit();

    const authz = authgebra.AuthorizationClient.init(&client);
    var outcome = try authz.check(.{
        .subject_id = "00000000-0000-4000-8000-000000000001",
        .permission = "document:read",
        .resource = .{ .resource_type_code = "document", .external_id = "doc-123" },
    }, .{});
    defer outcome.deinit();                           // every outcome owns an arena

    switch (outcome.result) {
        .ok => |ok| { _ = ok.data.decision; _ = ok.meta; _ = ok.ids.trace_id; },
        .api_error => |e| { _ = e.code; _ = e.retryable; _ = e.commit_state; },
        .transport_error => |t| { _ = t.kind; },
        .protocol_error => |p| { _ = p.kind; },
    }
}
```

A complete program is in `examples/check.zig` (`zig build run-example`).

## Results and errors — a tagged type, never a string

`Outcome(T).result` is exactly one of:

* **`ok`** — HTTP 2xx. `data` (typed), `meta` (`null` when the endpoint has none — **never 0**),
  `page`, `ids` (`request_id`, `trace_id`). `decision = deny` is a *successful* answer.
* **`api_error`** — the server's `{"error":{...}}` envelope: `status`, `code` (raw string, kept even
  when unknown) + `known_code`, `message`, `ids`, `violations` (ordered, JSON-Pointer `path` or
  `null`), `retryable` (`?bool`: true / false / not declared), `retry_after_seconds`,
  `commit_state`, `request_id_mismatch`, `details`.
* **`transport_error`** — no usable response: `timeout`, `connection_reset`, `connection_failed`,
  `tls`, `canceled`, `response_too_large`, `invalid_url`. `detail` is the Zig error name.
* **`protocol_error`** — a response that cannot be understood: `non_json_response`,
  `malformed_error_body`, `malformed_success_body`, `unexpected_redirect` (HTTP status kept).

Rules the SDK enforces:

* `503 NOT_READY` is **never** turned into a Deny. Apply your own safe default and keep the error.
* Unknown error codes are returned verbatim with `known_code = false`; they never map to a known one.
* Nothing is decided from `message` text.
* An unknown `decision` value is a `protocol_error` (fail closed), not a Deny or Allow.

## Tracing

* `CallOptions.request_id` is sent as `X-Request-ID` when valid (`[A-Za-z0-9._-]{8,128}`); invalid
  values are dropped, not sent.
* `CallOptions.traceparent` continues an existing W3C trace (malformed values are rejected locally).
  Otherwise (`propagate_trace = true`) a new `traceparent` is generated; retries keep the trace id and
  use a new span id.
* Responses: `ids.request_id` is the `X-Request-ID` header when valid, else the body value; a
  differing body value is exposed (`request_id_mismatch`, `body_request_id`). `ids.trace_id` is the
  body `error.trace_id` or the `X-Trace-ID` header — only if it is 32 lowercase hex and not all zero.
  `request_id`, `trace_id` and `span_id` are three different identifiers.
* Helpers: `authgebra.trace.newTraceparent`, `isValidTraceparent`, `traceIdOf`.

## Retries (bounded, conservative)

| Request | Default | Retried on |
|---|---|---|
| Read (GET/HEAD, and the read-only authz POSTs: check, batch-check, explain, list-resources) | 2 retries | `RATE_LIMITED` (honours `Retry-After`, capped by `max_delay_ms`), `NOT_READY`, timeout, connection reset/failed |
| Write | **0** — never auto-resent | Only if you raise `write_max_retries` **and** the call carries an `Idempotency-Key` (same key, same bytes): `NOT_READY`, `RATE_LIMITED`, `IDEMPOTENCY_IN_PROGRESS`, timeout, reset |

Hard caps: 10 retries for reads, 5 for writes. **Never retried**: `AUTHZ_RELOAD_PENDING` /
`IDENTITY_RELOAD_PENDING` (the mutation already committed; `commit_state = committed_unpublished`),
`CURSOR_STALE`, conflicts, validation and auth errors.

### Writes, idempotency and `AUTHZ_RELOAD_PENDING`

* Send `idempotency_key` on every admin write you may need to reconcile.
* `AUTHZ_RELOAD_PENDING`: the database transaction **committed** but the snapshot is not published
  yet. Do not resend, and do not report "the permission is effective" until you have confirmed it
  (e.g. `AuthorizationClient.revision()` shows `in_sync` at/after the write's revision).
* A write that **times out or loses its connection** yields `commit_state = unknown`: the SDK does
  not know whether it committed. Reconcile with the same `Idempotency-Key` or an entity read.
  Nothing is claimed (`commit_state = null`) when the connection was never established.

## Pagination

```zig
var pager = authz.pager(.{ .subject_id = id, .permission = "doc:read", .resource_type = "doc" }, .{ .max_pages = 100 });
defer pager.deinit();
while (try pager.next()) |page_outcome| {
    var o = page_outcome;
    defer o.deinit();
    // inspect o.result ...
}
```

The cursor is opaque and is passed back verbatim. The pager **never restarts on its own**: a
`CURSOR_STALE` is returned as an `api_error` and iteration ends — starting over is your explicit
decision (create a new pager). It is bounded by `max_pages`; cancel the surrounding `std.Io` task to
stop early (each page call also has the client deadline).

## Admin surface

`AdminClient` has one method per OpenAPI `operationId`, using named request models and typed
response data. `models` exports every component and named nested object or tagged-union variant.
Each declaration records its exact OpenAPI JSON Pointer. Fields that may be omitted use
`models.Optional(T)`: `.absent` omits the property, `.null` emits JSON null, and `.{ .value = v }`
emits the value, including false, zero and empty arrays. Nullable required fields use `?T` without
an omission default. HTTP JSON shapes remain unchanged.

`RawAdminClient` retains the original dynamic calls for compatibility and deliberately malformed
requests. Typed calls use `JsonValue` only for schema-declared arbitrary JSON and `Object(T)` for
dynamic-key objects with a known element type. `models.provenance.json` lists these exceptions,
their pointers and tests. Request model memory belongs to the caller; all decoded models and their
strings belong to the returned `Outcome` arena and are released by its existing `deinit()`.

Regenerate with `python3 tools/gen_admin.py`; `zig build check-generated` compares models,
operations, model roundtrip tests and provenance byte for byte without repairing the workspace.

## Configuration

`Config`: `base_url`, `token_provider` (called on every attempt), `total_timeout_ms` (default 30 s,
covers connect + TLS + send + receive — Zig 0.17 `std.http` has no separate connect-phase timeout for
TLS), `max_response_bytes` (default 8 MiB), `retry`, `extra_headers`, `propagate_trace`.
`User-Agent` is `authgebra-zig/<version>`. Header values containing control characters are refused
before anything is sent. Tokens only live in the outgoing `Authorization` header and are zeroed after
use; the SDK has no logging.

## Memory ownership

Explicit allocator everywhere (`Client.init(gpa, io, config)`). Each `Outcome` owns one arena;
call `outcome.deinit()` exactly once. Slices inside a result are valid until then. No global state.

Generated validation retains machine name bounds, the exact ASCII IAM code pattern and UUID-string
array length/uniqueness constraints. Other patterns or unsupported unique item schemas fail generation
explicitly. UUID-string uniqueness follows JSON value equality without case normalization.

Paged operations require an object `page` containing an explicit boolean `has_more`. Missing,
null, or incomplete page metadata is a protocol error and cannot silently terminate iteration.

## Development

```sh
zig build test --summary all   # unit, behaviour, fixtures, coverage; live tests skip without explicit env
zig build check-generated      # admin surface up to date versus sdks/contracts/openapi.json
zig build run-example          # needs AUTHGEBRA_URL / AUTHGEBRA_TOKEN / AUTHGEBRA_SUBJECT
```

Tests replay `sdks/fixtures/cases/*.json` against a loopback HTTP server (timeouts by stalling,
resets by dropping the connection). An `expect` key the harness does not understand fails the test.

Real CA trust, hostname and handshake failures are verified; see [status](../DISTRIBUTION.md). A stalled peer is
bounded only when `std.Io` provides concurrency (otherwise the call refuses to run unbounded and
reports `transport_error.other`).

## License

Proprietary — part of the Authgebra project (see the repository `LICENSE`). Not published to any
package registry.
