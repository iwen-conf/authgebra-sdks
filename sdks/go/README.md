# authgebra-go

Official Go client for the Authgebra HTTP API (`/api/v1`). It is a thin, typed wrapper — it never computes authorization decisions. Cross-language behavior is specified in [`../README.md`](../README.md) and verified against [`../fixtures/cases`](../fixtures/cases).

| | |
|---|---|
| Module | `github.com/iwen-conf/authgebra-sdks/sdks/go` (package `authgebra`) |
| SDK version | `0.1.0` (semver; independent of the server API version) |
| Supported API | `/api/v1` |
| OpenAPI digest | `sha256:907bd9616dba9993c479cf551bc1e69ac046776b72dd58f5af1138be10c34c25` (`../contracts/openapi.json`; checked by a test) |
| Go | `go.mod` declares **1.22**; developed and tested with **1.27.3**. Older toolchains are not verified. |
| Dependencies | none (stdlib only), no cgo |
| Status | **INTEGRATION_TESTED** — see [evidence and limits](../DISTRIBUTION.md); native and real integration tests passed |

## Install

The SDK is distributed from the public SDK-only repository under the proprietary commercial license.

```sh
go get github.com/iwen-conf/authgebra-sdks/sdks/go@v0.1.0
```

The earlier private checkout path `github.com/iwen-conf/authgebra/sdks/go` is not a public module.
Update imports to the public path when adopting 0.1.0; HTTP behavior is unchanged.

## Quick start

```go
client, err := authgebra.New(authgebra.Config{
    BaseURL: "https://authgebra.example.com",
    Token:   authgebra.StaticToken(os.Getenv("AUTHGEBRA_TOKEN")), // or a TokenFunc that refreshes
})
if err != nil { log.Fatal(err) }
defer client.Close()

res, err := client.Authorization.Check(ctx, authgebra.CheckRequest{
    SubjectID:  subjectID,
    Permission: "document:read",
    Resource:   &authgebra.AuthzResourceRef{ResourceTypeCode: "document", ExternalID: "doc-123"},
})
if err != nil { /* see Errors */ }
if res.Data.Allowed() { /* … */ }          // deny is a normal result
fmt.Println(res.Meta.AuthzRevision, res.TraceID)
```

Runnable examples: [`examples/check`](examples/check), [`batch`](examples/batch), [`paginate`](examples/paginate), [`idempotent_write`](examples/idempotent_write).

## Clients

* `client.Authorization` — `Check`, `BatchCheck`, `ListResources` (+ `ListResourcesPages` iterator), `Explain`, `Revision`.
* `client.Admin` — every management operation, grouped by area (`Admin.Users.CreateUser`, `Admin.Roles`, `Admin.Policies`, `Admin.Auth` (login/MFA/sessions), …). **101 of 101** OpenAPI operations are covered; `OperationMethods` maps each operationId to its method and a test fails if one is missing or fake.
* Login/refresh/health/setup need no bearer token: pass `authgebra.WithoutAuth()`.

Models and methods are generated from the OpenAPI export: `go generate ./...` (never edit `models_gen.go` / `ops_gen.go`; a test fails if they are stale).

## Errors

Results and errors are separate worlds; use `errors.As`:

| Type | Meaning |
|---|---|
| `*APIError` | Well-formed server error: `Status`, `Code` (raw string), `KnownCode`, `Message`, `RequestID`, `TraceID`, `Violations`, `Retryable *bool`, `RetryAfterSeconds`, `CommitState`. Unknown codes are preserved, never mapped to a known one. |
| `*TransportError` | No complete response: `Kind` = timeout / connection_reset / tls / canceled / network. For mutating requests `CommitState` is `unknown`. |
| `*ProtocolError` | A response arrived but is not understood (non-JSON, missing fields, too large). Keeps the HTTP status. |

* **A deny is not an error.** `503 NOT_READY` is *not* a deny: the system could not decide. Fail closed in your own code, but keep the original error for diagnosis.
* `errors.Is(err, &authgebra.APIError{Code: authgebra.CodeCursorStale})` matches by code. Never branch on `Message`.
* Admin reads carry no `meta`: `Result.Meta` is `nil` (never a fake revision 0).
* All revisions are `int64`; precision is lossless.

## Trace ID and request ID

Every call sends `X-Request-ID` (generated, or `WithRequestID`) and a W3C `traceparent` (new root, `WithTraceparent`, or `ContextWithTraceparent`). Retries reuse both. Invalid request IDs are discarded and replaced with a generated valid ID; invalid traceparent values are rejected before sending. Responses expose `RequestID` (header preferred, `RequestIDMismatch` flags header/body disagreement) and `TraceID` (body, then `X-Trace-ID`, then `traceparent`; invalid values ignored).

## Retries

Defaults: reads (GET/HEAD and the read-only decision POSTs) retry at most 2 times on `RATE_LIMITED` (honors `Retry-After`, capped), `NOT_READY`, timeouts and connection resets, with jittered backoff; hard cap 10. **Writes are never replayed by default.** Opt in with `RetryPolicy.RetryWrites` (or `WithRetryWrites(true)`): even then a write is only replayed if it carries an `Idempotency-Key`, with identical bytes. `CURSOR_STALE`, conflicts, 4xx and both `*_RELOAD_PENDING` codes are never retried.

## Idempotency and "committed but not published"

`503 AUTHZ_RELOAD_PENDING` / `IDENTITY_RELOAD_PENDING` mean the database change **is committed** but the snapshot is not published yet (`APIError.CommitState == committed_unpublished`, `Retryable == false`). Do not resend, and do not tell your caller the change is effective until you confirmed it (poll `Authorization.Revision`, or read the entity). A write that fails in transit has `CommitState == unknown`: re-send the *same* operation with the *same* `Idempotency-Key` to learn the outcome.

## Pagination

`ListResourcesPages` returns a pull-based iterator (`Next(ctx)`, `Page()`, `Err()`): bounded (`MaxPages`, default 100), cancellable through `ctx`, and it **never** restarts on `CURSOR_STALE` or merges pages from different snapshot generations (`ErrGenerationChanged`). Cursors are opaque.

## Safety defaults

TLS certificate verification is on (custom CAs via `Config.TLSConfig` or an injected `HTTPClient`); timeouts are finite (5 s connect, 30 s per attempt); responses are capped at 8 MiB and always closed; tokens never appear in errors or logs (`StaticToken` redacts itself; `Config.Logger` receives method, path template, status, ids only).

## Known limitations

* Optional fields stay omitted when nil. Generated `Field[T]` members preserve absent/null/value (including typed zero values and empty arrays), while legacy pointer members remain supported. The four machine-marked clearable PATCH fields support explicit null: `PatchUserRequest{ClearEmail: true}` and `ClearDescription: true` on role, policy and resource-type patches. Setting both the clear flag and a non-nil value fails before sending the request.
* Real-server scenarios and publication recovery passed; see [status](../DISTRIBUTION.md). Go 1.22.12 on Linux and Go 1.27.3 on macOS pass the native race, vet and generation checks.
* The `violations` of use-case-level 422 errors are not typed server-side yet; only request-body parse errors carry them.

## Development

```
go generate ./...        # regenerate from ../contracts/openapi.json
go vet ./...
go test -race ./...
```

See also [`CHANGELOG.md`](CHANGELOG.md) and [`LICENSE.md`](LICENSE.md).

## Generated types and source locations

All component objects, nested objects, enums, tagged unions and response envelopes have named models. Natural JSON members compose these models without inheritance or extra wire nesting. Each operation returns a concrete named `*OperationResult` alias, retaining compatibility with the existing instantiated `Result[Payload]` type. A shared schema retains its identity; create, patch, detail and list contracts are not merged by matching field names.

Each generated type and field records its OpenAPI JSON Pointer. `models.provenance.json` records the same mapping and machine constraints. Genuinely open schemas use `JSONValue` or typed dictionaries of it; `ParseJSONValue` validates and preserves 64-bit and arbitrary JSON number tokens. `RewriteRuleDto` and `TypedValueDto` use explicit variant members and reject empty, ambiguous and unknown tagged variants.

Use `SetField(value)` for an explicitly present zero/empty value and `NullField[T]()` for explicit null. Assign to the generated `NameField` member; do not simultaneously set its legacy `Name` member or a legacy `ClearName` flag. `Validate()` checks declared constraints without applying defaults or adding new transport-side rejection rules. Clear flags are generated only from `x-authgebra-clearable`, never from prose.

```sh
go run ./internal/gen -spec ../contracts/openapi.json -out . --check
go test -race -count=1 ./...
go vet ./...
```

The read-only check regenerates formatted bytes and compares all model, operation, provenance and generated serialization-test artifacts. Native mutation tests prove field deletion, JSON tag, nullable, enum and operation-request changes are detected without rewriting the damaged files.
