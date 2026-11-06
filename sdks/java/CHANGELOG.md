# Changelog

## 0.1.0 (unreleased)

- 2026-11-06: Generate named composed records and sealed unions, machine-driven JsonField PATCH states and concrete results. Discard invalid user Request IDs; reject scalar coercion and detect every generated-byte drift.

- Typed PATCH requests can explicitly clear email or descriptions while existing constructors still omit null fields; contradictory value/clear components fail locally.
- First implementation against the `/api/v1` contract (OpenAPI sha256 in `README.md`).
- `AuthorizationClient` (check, batch check, list + bounded page iterator, explain, revision).
- `AdminClient` generated for all 101 operations; typed records for every schema.
- Sealed error hierarchy (`ApiException`, `TransportException`, `ProtocolException`), `commitState`,
  tri-state `retryable`, violations, request/trace id handling.
- Bounded retry policy; writes only replay with an opted-in policy and an `Idempotency-Key`.
- Not published to any registry.

- 2026-11-04: native live-server scenarios and committed-unpublished recovery passed; status INTEGRATION_TESTED, not RELEASE_READY. See `../STATUS.md`.
