"""Typed request and result models (frozen dataclasses, no third-party model library).

Only fields the server defines are sent.  ``None`` means *omit the field*; the SDK never
fills in defaults, so the meaning of "absent" vs ``null`` vs ``{}`` is the caller's choice
(``context=None`` omits it, ``context={}`` sends ``{}``).

Revisions and generations are plain ``int`` (arbitrary precision): ``authz_revision`` of
``9223372036854775807`` survives the round trip.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Sequence, Union

if TYPE_CHECKING:
    from ._generated import models as api_models

JSONValue = Union[None, bool, int, float, str, List["JSONValue"], Dict[str, "JSONValue"]]


class Decision(str, Enum):
    """Authorization decision. Any other value from the server is a ``ProtocolError``."""

    ALLOW = "allow"
    DENY = "deny"


def _uuid(value: uuid.UUID | str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc


@dataclass(frozen=True)
class ResourceRef:
    """Stable external identity of a resource (never an internal slot)."""

    resource_type_code: str
    external_id: str

    def to_json(self) -> dict[str, Any]:
        return {"resource_type_code": self.resource_type_code, "external_id": self.external_id}


@dataclass(frozen=True)
class Meta:
    """``meta`` of a response. Absent on admin reads: the SDK then exposes ``None``, never 0."""

    authz_revision: int
    snapshot_generation: int | None


@dataclass(frozen=True)
class PageInfo:
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class ResponseInfo:
    """Correlation data of one HTTP exchange."""

    status: int
    request_id: str | None
    trace_id: str | None
    request_id_mismatch: bool = False


@dataclass(frozen=True)
class ApiResult:
    """Generic successful response (used by the admin API and the low-level ``request``)."""

    data: Any
    info: ResponseInfo
    meta: Meta | None = None
    page: PageInfo | None = None
    etag: str | None = None
    #: ``Idempotency-Replayed: true`` was present (the server replayed a stored response).
    replayed: bool = False
    #: ``X-Authz-Revision`` response header, when present and numeric.
    authz_revision_header: int | None = None


@dataclass(frozen=True)
class CheckRequest:
    subject_id: uuid.UUID | str
    permission: str
    resource: ResourceRef | None = None
    context: Mapping[str, JSONValue] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject_id": _uuid(self.subject_id, "subject_id"),
            "permission": self.permission,
        }
        if self.resource is not None:
            body["resource"] = self.resource.to_json()
        if self.context is not None:
            body["context"] = dict(self.context)
        return body


@dataclass(frozen=True)
class BatchCheckRequest:
    subject_id: uuid.UUID | str
    permission: str
    resources: Sequence[ResourceRef]
    context: Mapping[str, JSONValue] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject_id": _uuid(self.subject_id, "subject_id"),
            "permission": self.permission,
            "resources": [r.to_json() for r in self.resources],
        }
        if self.context is not None:
            body["context"] = dict(self.context)
        return body


@dataclass(frozen=True)
class ListResourcesRequest:
    subject_id: uuid.UUID | str
    permission: str
    resource_type: str
    limit: int | None = None
    cursor: str | None = None
    context: Mapping[str, JSONValue] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject_id": _uuid(self.subject_id, "subject_id"),
            "permission": self.permission,
            "resource_type": self.resource_type,
        }
        if self.limit is not None:
            body["limit"] = self.limit
        if self.cursor is not None:
            body["cursor"] = self.cursor
        if self.context is not None:
            body["context"] = dict(self.context)
        return body

    def with_cursor(self, cursor: str | None) -> ListResourcesRequest:
        return ListResourcesRequest(
            self.subject_id,
            self.permission,
            self.resource_type,
            self.limit,
            cursor,
            self.context,
        )


@dataclass(frozen=True)
class ExplainRequest:
    subject_id: uuid.UUID | str
    permission: str
    resource: ResourceRef | None = None
    context: Mapping[str, JSONValue] | None = None
    #: Passed through verbatim (``{"max_steps": ...}``-style options defined by the server).
    trace: api_models.ExplainTraceOptions | Mapping[str, JSONValue] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject_id": _uuid(self.subject_id, "subject_id"),
            "permission": self.permission,
        }
        if self.resource is not None:
            body["resource"] = self.resource.to_json()
        if self.context is not None:
            body["context"] = dict(self.context)
        if self.trace is not None:
            body["trace"] = dict(self.trace)
        return body


@dataclass(frozen=True)
class CheckResult:
    decision: Decision
    reason_code: str
    matched_policy: api_models.AuthzPolicyRef | None
    info: ResponseInfo
    meta: Meta | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass(frozen=True)
class BatchDecision:
    resource: ResourceRef
    reason_code: str
    matched_policy: api_models.AuthzPolicyRef | None


@dataclass(frozen=True)
class BatchCheckResult:
    allowed: tuple[BatchDecision, ...]
    denied: tuple[BatchDecision, ...]
    info: ResponseInfo
    meta: Meta | None = None


@dataclass(frozen=True)
class ResourcePage:
    items: tuple[ResourceRef, ...]
    page: PageInfo
    info: ResponseInfo
    meta: Meta | None = None


@dataclass(frozen=True)
class ExplainResult:
    decision: Decision
    reason_code: str
    matched_policy: api_models.AuthzPolicyRef | None
    #: Policy explanations as returned by the server (the SDK does not reinterpret them).
    policies: tuple[api_models.AuthzPolicyExplanation, ...]
    truncated: bool
    total_steps: int
    info: ResponseInfo
    meta: Meta | None = None


@dataclass(frozen=True)
class RevisionResult:
    db_revision: int
    snapshot_generation: int
    #: ``in_sync`` / ``rebuilding`` / ``stale`` (kept as text so new states do not break clients).
    status: str
    info: ResponseInfo
    meta: Meta | None = None


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries; never unbounded.

    Reads retry on ``RATE_LIMITED`` / ``NOT_READY`` / timeout / connect / reset.  Writes are
    never retried unless ``retry_writes`` is set **and** the call carries an
    ``Idempotency-Key``.  ``AUTHZ_RELOAD_PENDING`` / ``IDENTITY_RELOAD_PENDING`` are never retried.
    """

    max_retries: int = 2
    base_delay: float = 0.2
    #: Upper bound for any single wait, including a server ``Retry-After``.
    max_delay: float = 5.0
    jitter: float = 0.2
    retry_writes: bool = False
    sleep: Any = field(default=None, compare=False)
    async_sleep: Any = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries must be within 0..=10 (retries are always bounded)")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
