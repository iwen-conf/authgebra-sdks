"""Official Python client for the Authgebra ``/api/v1`` HTTP API.

A thin wrapper: it calls the server and interprets responses; it never evaluates
RBAC/ABAC/ReBAC itself.  See ``README.md`` for semantics (results vs errors, retries, tracing).
"""

from ._core import new_idempotency_key, new_traceparent, parse_traceparent
from ._generated.operations import OPERATIONS, SPEC_SHA256
from ._generated import models as api_models
from ._model import JSONValue, UNSET, Unset
from ._version import __version__
from .aclient import AsyncAuthorizationClient, AsyncClient
from .client import AuthorizationClient, Client
from .config import Config
from .errors import (
    ApiError,
    AuthgebraError,
    CommitState,
    CursorStaleError,
    NotReadyError,
    PaginationError,
    ProtocolError,
    RateLimitedError,
    ReloadPendingError,
    TransportError,
    Violation,
)
from .models import (
    ApiResult,
    BatchCheckRequest,
    BatchCheckResult,
    BatchDecision,
    CheckRequest,
    CheckResult,
    Decision,
    ExplainRequest,
    ExplainResult,
    ListResourcesRequest,
    Meta,
    PageInfo,
    ResourcePage,
    ResourceRef,
    ResponseInfo,
    RetryPolicy,
    RevisionResult,
)

__all__ = [
    "api_models",
    "JSONValue",
    "UNSET",
    "Unset",
    "OPERATIONS",
    "SPEC_SHA256",
    "ApiError",
    "ApiResult",
    "AsyncAuthorizationClient",
    "AsyncClient",
    "AuthgebraError",
    "AuthorizationClient",
    "BatchCheckRequest",
    "BatchCheckResult",
    "BatchDecision",
    "CheckRequest",
    "CheckResult",
    "Client",
    "CommitState",
    "Config",
    "CursorStaleError",
    "Decision",
    "ExplainRequest",
    "ExplainResult",
    "ListResourcesRequest",
    "Meta",
    "NotReadyError",
    "PageInfo",
    "PaginationError",
    "ProtocolError",
    "RateLimitedError",
    "ReloadPendingError",
    "ResourcePage",
    "ResourceRef",
    "ResponseInfo",
    "RetryPolicy",
    "RevisionResult",
    "TransportError",
    "Violation",
    "__version__",
    "new_idempotency_key",
    "new_traceparent",
    "parse_traceparent",
]
