# Authgebra.Client (C#)

Official .NET client for the Authgebra `/api/v1` authorization HTTP API. It is a thin wrapper:
it calls the server and **never computes authorization itself** (no RBAC/ABAC/ReBAC in the SDK).

| | |
|---|---|
| Verification | **INTEGRATION_TESTED** — [evidence and limits](../DISTRIBUTION.md); native and real integration tests passed |
| Package id | `Authgebra.Client` (0.1.0 GitHub package; NuGet.org tracked separately) |
| SDK version | 0.1.0 (versioned independently of the server) |
| Target / minimum runtime | `net8.0` (.NET 8 LTS) — built and tested with SDK 8.0.427 |
| Server contract | `/api/v1`, generated from `sdks/contracts/openapi.json` |
| Contract digest (sha256) | `907bd9616dba9993c479cf551bc1e69ac046776b72dd58f5af1138be10c34c25` |
| Dependencies | `Microsoft.Extensions.Http` 8.0.1 (for the DI helper and `ILogger`) |

## Install from the GitHub Release package

```bash
# Place Authgebra.Client.0.1.0.nupkg in ./packages
dotnet add package Authgebra.Client --version 0.1.0 --source ./packages
```

or reference `src/Authgebra.Client/Authgebra.Client.csproj` directly.

## Quick start

```csharp
using Authgebra;

using var client = new AuthgebraClient(new AuthgebraClientOptions
{
    BaseUrl = "https://authgebra.example.com",
    TokenProvider = new StaticTokenProvider(accessToken), // or your own ITokenProvider
});

// Decisions: deny is a *successful* result, not an exception.
var result = await client.Authorization.CheckAsync(new CheckRequest
{
    SubjectId = subjectId,
    Permission = "document:read",
    Resource = new AuthzResourceRef { ResourceTypeCode = "document", ExternalId = "doc-123" },
});
Console.WriteLine($"{result.Data.Decision} rev={result.Meta?.AuthzRevision} gen={result.Meta?.SnapshotGeneration} trace={result.TraceId}");
```

`BatchCheckAsync`, `ExplainAsync`, `RevisionAsync` and `ListResourcesAsync` live on `client.Authorization`.
All 101 contract operations are available on `client.Admin.<Group>` (`Users`, `Roles`, `Permissions`,
`Resources`, `ResourceTypes`, `Attributes`, `Policies`, `PolicyBindings`, `RelationDefinitions`,
`RelationTuples`, `Audit`, `Dashboard`, `Auth`, `Setup`, `Health`); method name = `operationId` in
PascalCase + `Async`.

### Dependency injection

```csharp
services.AddAuthgebra(o => { o.BaseUrl = "https://authgebra.example.com"; o.TokenProvider = myProvider; });
// inject AuthgebraClient (singleton; HttpClient comes from IHttpClientFactory)
```

You can also pass your own `HttpClient` to the constructor; the SDK neither disposes nor reconfigures it.

## Errors

| Exception | When |
|---|---|
| `ApiException` | the server returned the contract error body. `Code` is the **raw string** (`KnownCode` tells whether this SDK knows it), plus `Status`, `RequestId`, `TraceId`, `Violations`, `Retryable` (`true`/`false`/`null` = not stated), `RetryAfterSeconds`, `CommitState`, `RequestIdMismatch`. |
| `CursorStaleException` (an `ApiException`) | `CURSOR_STALE`: restart pagination yourself. |
| `TransportException` | timeout / connection reset / connect failure / TLS. For writes `CommitState == Unknown`. |
| `ProtocolException` | non-JSON body, missing required fields, response too large. Keeps the HTTP status. |
| `PagerException` | page limit exceeded, or `snapshot_generation` changed between pages. |

Never branch on `Message`/`ServerMessage`. `503 NOT_READY` means the system could not reach a trustworthy
decision — it is **not** a deny. Apply your own fail-closed policy and keep the original exception for diagnosis.
`RequestId`/`TraceId` are what the **server** reported; `SentRequestId`/`SentTraceParent` are what this client sent.

## Tracing

Every request carries a valid `X-Request-ID` and a W3C `traceparent`: the one you pass in
`RequestOptions.TraceParent`, else the current `Activity`, else a freshly generated one. Invalid values are rejected
locally before anything is sent. Request id, trace id and span id are different things and are not mixed.

## Pagination

```csharp
await foreach (var page in client.Authorization.ListResourcesPagesAsync(request))
    foreach (var r in page.Data) { /* ... */ }
```

The iterator is pull-based and cancellable, capped at `PagerOptions.MaxPages` (default 1000), throws
`CursorStaleException` on `CURSOR_STALE` (it never silently restarts) and refuses to merge pages from different
`snapshot_generation`s. Cursors are opaque; pass them back unchanged.

## Retries, idempotency and "committed but not published"

- Reads (GET and the side-effect-free authorization POSTs) retry at most `RetryPolicy.MaxRetries` times (default 2,
  hard cap 10) on `RATE_LIMITED` (honouring `Retry-After`, capped), `NOT_READY`, timeouts and connection failures.
- **Writes are never resent by default.** They are only retried if you set `RetryWrites = true` **and** the call has an
  `Idempotency-Key`; the same bytes are sent each time.
- `AUTHZ_RELOAD_PENDING` / `IDENTITY_RELOAD_PENDING` mean the transaction **already committed** but the snapshot is not
  published yet. They are never retried; `CommitState == CommittedUnpublished`. Do not report "authorization is in
  effect" until you have confirmed publication (e.g. read the entity / `RevisionAsync`).
- A write that times out has `CommitState == Unknown` (not "not committed"). Reconcile with the same idempotency key
  or by reading the entity.

## Requests

Optional fields you leave unset are omitted from the JSON. For nullable, optional fields use `Optional<T>`:
`Optional<string>.Unset` (omit), `Optional<string>.Null` (send `null`), or a value. Whether null clears
or has another meaning comes from that field's contract, including its machine-readable clearable marker. 64-bit revisions are `long`
and are parsed without going through floating point. BMP characters are written as-is; supplementary characters (e.g. emoji)
may be written as `\uXXXX` surrogate pairs, which is valid JSON.

All component schemas, nested objects and known union variants have generated named models.
`RewriteRuleDto` and `TypedValueDto` compose one typed variant without a model inheritance tree.
String enums have named value types and constants; unknown error codes still retain their original
string in `ApiException`. Schema-declared arbitrary values use `JsonValue`, and dynamic-key maps
retain their schema-defined element type. `JsonValue` clones its `JsonElement` so it survives disposal
of the source document. The existing `Optional<JsonElement>` construction path remains convertible
to typed optional fields.

Response optional fields expose `XField: Optional<T>` for exact presence/null/value information and
retain the previous nullable `X` property as a compatibility view. Generated `Validate()` methods
allow explicit checks against the machine constraints without changing the existing client's local
rejection policy. Deserialization checks required key presence and nullability separately from
constraint validation, so missing fields cannot silently become zero values; unknown fields and
unknown string enum values remain accepted. Paged operations require an object `page` with an
explicit boolean `has_more`, rather than interpreting malformed metadata as the end of a cursor.
The IAM code pattern is checked as exact ASCII characters, including rejection of trailing newlines;
array uniqueness uses structural JSON equality. Unsupported patterns fail generation explicitly.
Closed request roots and closed models reachable only from requests reject unknown JSON fields on
import. Models shared with responses retain unknown-field forward compatibility.
Every generated declaration points to its OpenAPI JSON Pointer;
`Generated/Models.provenance.json` records fields, constraints, dynamic exceptions and proven payload
aliases. Envelope payloads reuse a component only when the wrapper identifies it and its complete
schema matches that component.

## Security defaults

TLS certificates are validated by the platform (there is no switch to turn that off; inject your own `HttpClient` if you
truly must). Plain `http://` base URLs are rejected unless `AllowInsecureHttp` is set. Timeouts are finite (connect 5 s,
request 30 s). Response bodies are capped at 8 MiB. Tokens are never logged or put in exception text.

## Development

```bash
python3 tools/gen.py            # regenerate src/Authgebra.Client/Generated/*.g.cs from ../contracts/openapi.json
python3 tools/gen.py --check    # generate into a temporary directory and compare every artifact byte for byte
dotnet test tests/Authgebra.Client.Tests
```

Tests run every case in `sdks/fixtures/cases` plus operation-coverage, retry, tracing, pager and security tests.

## Known limits

- Live-server scenarios and publication recovery passed; see [evidence and remaining limits](../DISTRIBUTION.md).
- The OpenAPI document currently types `decision` and `reason_code` as plain strings and declares no
  `securitySchemes`; the SDK follows the contract as written.
- Not published; no signing; no source link.
