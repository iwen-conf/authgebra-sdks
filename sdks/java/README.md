# authgebra-java

Official Java client for the Authgebra `/api/v1` HTTP API. A thin wrapper: it calls the server and
**never evaluates RBAC/ABAC/ReBAC itself**.

| | |
|---|---|
| Coordinates | `com.authgebra:authgebra-java:0.1.0` (GitHub distribution; Maven Central tracked separately) |
| Language level | Java 17 bytecode (`--release 17`) — see "Verified on" |
| Runtime dependency | `jackson-databind` only (no Spring, no HTTP library: `java.net.http`) |
| Server contract | `/api/v1`, OpenAPI sha256 `907bd9616dba9993c479cf551bc1e69ac046776b72dd58f5af1138be10c34c25` (101 operations) |
| Status | **INTEGRATION_TESTED** (see [SDK status](../DISTRIBUTION.md)); native and real integration tests passed |

## Install

Download the JAR and POM from the 0.1.0 GitHub Release, then install both into your local Maven repository:

```bash
mvn install:install-file -Dfile=authgebra-java-0.1.0.jar -DpomFile=authgebra-java-0.1.0.pom
```
```xml
<dependency>
  <groupId>com.authgebra</groupId>
  <artifactId>authgebra-java</artifactId>
  <version>0.1.0</version>
</dependency>
```

## Quick start

```java
var client = AuthgebraClient.create(Config.builder()
        .baseUrl("https://authgebra.example.com")          // https required (loopback http allowed)
        .tokenProvider(() -> tokens.current())               // called before every attempt; null = anonymous
        .build());

var result = client.authorization().check(new Models.CheckRequest(
        null, "document:read", new Models.AuthzResourceRef("doc-123", "document"), subjectId));

switch (AuthorizationClient.decision(result)) {
    case ALLOW -> { /* proceed */ }
    case DENY -> { /* a normal, successful answer */ }
    case UNKNOWN -> { /* a value this SDK does not know: treat as not allowed */ }
}
result.authzRevision();      // Optional<Long>; empty when the server sent no meta — never a fake 0
result.snapshotGeneration(); // Optional<Long>
result.traceId();            // Optional<String>, W3C trace id
result.requestId();          // Optional<String>
```

`client.authorization()` has `check`, `batchCheck`, `listResources` (+ `listResourcePages`), `explain`,
`revision`. `client.admin()` exposes **all 101 operations** of the contract grouped by tag
(`users()`, `roles()`, `policies()`, `resources()`, `attributes()`, `auth()`, …); `client.raw()` is an untyped
escape hatch. Runnable samples: `examples/` (compiled by the test suite).

Authentication: this SDK sends `Authorization: Bearer <token>`. Obtain the token through the `auth()` group
(`login`, `refresh`, …) and feed it to your `TokenProvider`. Refresh-cookie handling is your responsibility.

## Results vs errors

* 2xx → `Result<T>` (`data`, optional `meta`, `requestId`, `traceId`). **`decision=deny` is a result.**
* `AuthgebraException` is sealed with three families:
  * `ApiException` — well-formed server error: `status`, `code` (verbatim), `knownCode`, `violations`,
    `retryable` (`Boolean`, `null` = server made no statement), `retryAfterSeconds`, `commitState`,
    `requestId`, `traceId`, `requestIdMismatch`. `CursorStaleException` is a subtype.
  * `TransportException` — `TIMEOUT`, `CONNECTION_RESET`, `CONNECT_FAILED`, `TLS`, `CANCELLED`.
  * `ProtocolException` — non-JSON body, missing required fields, oversized response, generation change.
* **`NOT_READY` is never turned into a deny.** The system could not decide; apply your own fail-closed policy
  and keep the exception.
* Unknown error codes are kept as-is (`knownCode() == false`) and are never mapped to a known code. Never branch
  on `getMessage()`/`serverMessage()`.

## Tracing

Every call sends `X-Request-ID` and a W3C `traceparent` (generated if you do not pass one; one trace id per
logical call, a fresh span id per attempt). Pass your own with
`CallOptions.builder().traceparent(tp).tracestate(ts).requestId(id)`. The response `X-Request-ID` wins over the
body value; if both exist and differ, `requestIdMismatch()` is `true`. Invalid ids/headers are ignored, not trusted.

## Retries (conservative, bounded)

* Reads (GET, and the authz decision POSTs): up to 2 retries on `RATE_LIMITED` (honours `Retry-After`, capped
  at 10 s), `NOT_READY`, timeouts, resets. `CURSOR_STALE`, `FORBIDDEN` and every other 4xx are never retried.
* Writes: **never retried by default.** Opt in with `RetryPolicy.builder().retryIdempotentWrites(true)`; even then
  only calls carrying an `Idempotency-Key` are replayed (same key, same bytes), only on `NOT_READY`,
  `IDEMPOTENCY_IN_PROGRESS` and transport failures.
* **`AUTHZ_RELOAD_PENDING` / `IDENTITY_RELOAD_PENDING` are never replayed.** They mean the write **committed** and
  the snapshot is not published yet (`commitState() == "committed_unpublished"`). Do not report "authorization
  is in effect" until you have confirmed publication (e.g. `authorization().revision()` shows
  `snapshot_generation >= meta.authz_revision` of your write).
* A write that times out or whose connection breaks has `TransportException.commitState() == "unknown"`
  (SDK inference): it may have been applied. Verify with the same `Idempotency-Key` or by reading the entity.
* JDK note: `java.net.http.HttpClient` itself may transparently re-send an idempotent request once when a
  connection is closed before any response. That is below this SDK; non-idempotent POSTs are not affected.

## Pagination

`listResourcePages(request, maxPages, options)` is bounded (`maxPages` ≥ 1), cancellable (`cancel()`) and
exposes `truncated()`. It **throws** `CursorStaleException` on `CURSOR_STALE` and
`ProtocolException(generation_changed)` if a page reports a different `snapshot_generation`; it never restarts
or stitches generations together. Cursors are opaque strings: do not parse or modify them.

## Safety and resources

TLS certificate and host name verification are always on (inject your own `HttpClient` for custom trust).
Plain `http://` is accepted only for loopback unless `allowInsecureHttp(true)`. Timeouts are finite (connect 5 s,
request 30 s, also bounding a stalled body); response bodies are capped (8 MiB default) and always closed. Tokens
are never logged or put in exception text; the logger receives operation id, method, path template, request id,
status and attempt number only.

## Limits you should know

* Optional properties use `JsonField<T>` to preserve absence, explicit null, typed zero values and empty arrays.
  Existing constructors and typed accessors remain available: their `null` arguments still mean absence.
  Use `Models.PatchUserRequest.fromFields(JsonField.absent(), JsonField.nullValue())` for explicit states,
  or the compatible `.withNullEmail()` / `.withNullDescription()` helpers for machine-marked clearable PATCH fields.
  Required nullable properties retain explicit null, and non-nullable properties reject a null field state.
* `date-time` fields remain `String` (RFC 3339 as sent, no timezone conversion). Enums have named value records
  and constants; legacy string constructors/accessors remain, and unknown response values survive.
* Tagged `RewriteRuleDto` and `TypedValueDto` alternatives have named records, retaining their original JSON
  discriminator and scalar types. `uint32` uses `Long`, `uint64` uses `BigInteger`; legacy integer constructor
  arguments convert exactly, rejecting truncation and overflow.
* Generated `validate()` methods check declared required fields, numeric bounds, lengths, array bounds and
  nested models without applying defaults or introducing automatic transport-side rejection.
* Import untrusted request JSON with the generated `CheckRequest.fromJson(mapper, node)` factory (and its
  counterparts). It validates unknown nested properties, required/null states and scalar shapes before
  constructing the model. Shared DTOs still ignore new response fields when read through Jackson.
* 64-bit values (`authz_revision`, `snapshot_generation`, …) are `Long` and parsed from the raw bytes: no
  precision loss (tested with `9223372036854775807` and `9007199254740993`).

## Development

```bash
cd sdks/java
mvn -B test                    # unit + the 25 shared fixtures + contract coverage + examples compile
java tools/Generate.java       # regenerate models, operations and field provenance
java tools/Generate.java --check # regenerate in an isolated directory and compare every generated byte
```
`ContractCoverageTest` fails if `contracts/openapi.json` changed without regenerating, or if any operation lacks a
method. Native reflection checks each component property against its actual Java JSON member and required flag;
mutation tests prove corrupted fields, tags, nullability, enums and operation signatures fail the byte check.
`models.provenance.json` maps types and members to their exact OpenAPI JSON Pointers. Only explicitly open
schemas retain dynamic JSON; unsupported structural schemas fail generation. Never edit generated files by hand.

## Verified on

Temurin Java 17.0.19 and JDK 25.0.4, macOS. Java 17 executes the full suite and an independent jar consumer.
Real CA trust, hostname and handshake failures are verified; live scenarios and publication recovery are recorded in [status](../DISTRIBUTION.md).
