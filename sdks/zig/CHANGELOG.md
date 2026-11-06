# Changelog

## 0.1.0 (unreleased)

- 2026-11-06: Generate named management request/response models, enums and unions while preserving Outcome arena ownership. Keep RawAdminClient as an explicit compatibility path; add machine constraints, pagination shape validation and byte-exact generation checks.

First implementation. Targets API `/api/v1` and Zig 0.17.0.

* Typed `AuthorizationClient`: check, batchCheck, explain, listResources (+ pager), revision.
* `AdminClient`: 101 generated methods, one per OpenAPI operationId.
* Tagged `Outcome` (ok / api_error / transport_error / protocol_error), `commit_state`,
  tri-state `retryable`, violations, request-id/trace-id handling.
* Bounded retry policy; writes are never auto-resent without an Idempotency-Key;
  reload-pending codes are never resent.
* Tests: 25 shared fixtures, retry/never-resend/tracing/pagination behaviour, operation coverage.

- 2026-11-04: native live-server scenarios and committed-unpublished recovery passed; status INTEGRATION_TESTED, not RELEASE_READY. See `../STATUS.md`.
