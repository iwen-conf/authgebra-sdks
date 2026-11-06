# Changelog

Versioned independently from the Authgebra server. Each release records the `/api/v1`
contract it was generated from (`sdks/contracts/openapi.sha256`).

## 0.1.0 — unreleased

- 2026-11-06: Generate composed models, strong tagged variants and Optional wire fields. Require mandatory response members without closing unknown response enums; align invalid Request ID handling and preserve deterministic field provenance.

- First implementation: `AuthgebraClient` with `Authorization` (Check, BatchCheck,
  ListResources + pager, Explain, Revision) and `Admin` (all 101 operations of the contract).
- Typed errors (`ApiException`, `CursorStaleException`, `TransportException`, `ProtocolException`,
  `PagerException`), W3C trace propagation, bounded retries, no automatic write replay.
- Not published to any registry.

- 2026-11-04: native live-server scenarios and committed-unpublished recovery passed; status INTEGRATION_TESTED, not RELEASE_READY. See `../STATUS.md`.
