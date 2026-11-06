"""Typed error hierarchy.

Three distinct failure worlds (see ``sdks/README.md``):

* :class:`ApiError` - the server answered with a well-formed ``{"error": {...}}`` body.
* :class:`TransportError` - no usable HTTP response (timeout, reset, TLS ...).
* :class:`ProtocolError` - an HTTP response arrived but cannot be understood.

``decision = deny`` is **not** an error; it is a normal result.  ``NOT_READY`` is an
error and must never be converted into a deny by this SDK.
Callers must branch on ``code`` / subclasses, never on ``message`` text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class CommitState(str, Enum):
    """What is known about a write.

    ``COMMITTED_UNPUBLISHED`` is declared by the server (the mutation *did* execute).
    ``UNKNOWN`` is inferred by the SDK for a write whose transport failed: the
    mutation may or may not have been applied.  ``None`` means "nothing is known".
    """

    COMMITTED_UNPUBLISHED = "committed_unpublished"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Violation:
    """One entry of ``error.details.violations``."""

    path: str | None
    reason: str
    message: str


class AuthgebraError(Exception):
    """Base class for every error raised by this SDK."""

    attempts: int = 1


class ApiError(AuthgebraError):
    """The server returned an error envelope."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        known_code: bool,
        message: str,
        request_id: str | None,
        body_request_id: str | None,
        request_id_mismatch: bool,
        request_id_header_valid: bool,
        trace_id: str | None,
        details: Mapping[str, Any] | None,
        violations: tuple[Violation, ...],
        retryable: bool | None,
        retry_after_seconds: int | None,
        commit_state: CommitState | None,
        raw_body_excerpt: str,
    ) -> None:
        super().__init__(f"{code} (HTTP {status}): {message}")
        self.status = status
        #: The raw code string exactly as sent (unknown codes are preserved).
        self.code = code
        #: Whether ``code`` belongs to the set known when this SDK was built.
        self.known_code = known_code
        self.message = message
        #: Taken from the ``X-Request-ID`` header when valid, otherwise from the body.
        self.request_id = request_id
        self.body_request_id = body_request_id
        self.request_id_mismatch = request_id_mismatch
        self.request_id_header_valid = request_id_header_valid
        self.trace_id = trace_id
        self.details = details
        self.violations = violations
        #: Tri-state hint: True / False / None (server makes no statement).
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.commit_state = commit_state
        #: Truncated body text, safe to log (the server redacts secrets).
        self.raw_body_excerpt = raw_body_excerpt

    def __str__(self) -> str:
        base = f"{self.code} (HTTP {self.status}): {self.message}"
        extra = []
        if self.request_id:
            extra.append(f"request_id={self.request_id}")
        if self.trace_id:
            extra.append(f"trace_id={self.trace_id}")
        return f"{base} [{', '.join(extra)}]" if extra else base


class CursorStaleError(ApiError):
    """``CURSOR_STALE``: restart pagination yourself; the SDK never does it silently."""


class NotReadyError(ApiError):
    """``NOT_READY``: the system could not reach a trustworthy decision. Not a deny."""


class RateLimitedError(ApiError):
    """``RATE_LIMITED``."""


class ReloadPendingError(ApiError):
    """``AUTHZ_RELOAD_PENDING`` / ``IDENTITY_RELOAD_PENDING``.

    The mutation was **committed** but is not yet published.  Never resend it.
    """


API_ERROR_CLASSES: Mapping[str, type[ApiError]] = {
    "CURSOR_STALE": CursorStaleError,
    "NOT_READY": NotReadyError,
    "RATE_LIMITED": RateLimitedError,
    "AUTHZ_RELOAD_PENDING": ReloadPendingError,
    "IDENTITY_RELOAD_PENDING": ReloadPendingError,
}


class TransportError(AuthgebraError):
    """No usable HTTP response.

    ``error`` is one of ``timeout``, ``connect_error``, ``tls_error``,
    ``connection_reset``, ``transport``.  ``commit_state`` is
    :attr:`CommitState.UNKNOWN` for writes (never assume "not committed").
    """

    def __init__(
        self,
        error: str,
        *,
        commit_state: CommitState | None,
        request_id: str | None,
        detail: str = "",
    ) -> None:
        super().__init__(f"transport error: {error}" + (f" ({detail})" if detail else ""))
        self.error = error
        self.commit_state = commit_state
        self.request_id = request_id


class ProtocolError(AuthgebraError):
    """A response arrived but violates the contract.

    ``error`` is one of ``non_json_response``, ``malformed_error_body``,
    ``malformed_success_body``, ``response_too_large``, ``unknown_decision``.
    """

    def __init__(
        self,
        error: str,
        *,
        status: int | None,
        request_id: str | None = None,
        trace_id: str | None = None,
        excerpt: str = "",
    ) -> None:
        super().__init__(f"protocol error: {error}" + (f" (HTTP {status})" if status else ""))
        self.error = error
        self.status = status
        self.request_id = request_id
        self.trace_id = trace_id
        self.excerpt = excerpt


class PaginationError(AuthgebraError):
    """The paging iterator refused to continue (limit reached, cursor loop, generation change)."""
