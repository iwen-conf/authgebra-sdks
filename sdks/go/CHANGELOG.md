# Changelog

## 0.1.0 — unreleased

- 2026-11-06: Named nested models and tagged unions; concrete operation result aliases; Field[T] preserves absent/null/value with legacy pointer/clear paths. Generated request validation reads machine metadata, and every generated artifact has a read-only byte check.
- Typed PATCH requests can explicitly clear email or descriptions without changing existing pointer fields; conflicting value/clear requests fail locally.
- First implementation: client core (config, bearer token provider, injectable HTTP client, bounded retries, request-id and W3C trace propagation, safe logging), typed errors (`APIError`, `TransportError`, `ProtocolError`), `Authorization` client with a bounded, stale-aware page iterator, and a generated `Admin` client covering all 101 operations of the reviewed OpenAPI export.
- Verified against the 25 shared fixtures in `../fixtures/cases`. Not published; live-server verification is recorded in `../STATUS.md`.

- 2026-11-04: native live-server scenarios and committed-unpublished recovery passed; status INTEGRATION_TESTED, not RELEASE_READY. See `../STATUS.md`.
