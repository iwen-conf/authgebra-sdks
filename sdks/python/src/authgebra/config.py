"""Client configuration."""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .models import RetryPolicy

#: Headers the SDK owns; callers cannot override them through ``Config.headers``.
RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "content-type",
        "accept",
        "user-agent",
        "x-request-id",
        "traceparent",
        "tracestate",
        "idempotency-key",
        "if-match",
        "host",
        "content-length",
    }
)


@dataclass(repr=False)
class Config:
    """Settings shared by :class:`~authgebra.Client` and :class:`~authgebra.AsyncClient`.

    * ``token_provider`` is called before **every** attempt (so refreshed tokens are used).  The
      async client also accepts an ``async def`` provider.  Tokens are never logged or put in errors.
    * ``verify`` is ``True`` (system CAs), a CA bundle path, or an ``ssl.SSLContext``.
      Disabling verification is intentionally not supported.
    * ``timeout`` is the total wall-clock budget of one attempt; ``connect_timeout`` bounds connect.
    * ``http_client`` / ``transport`` inject your own ``httpx`` client or transport; an injected
      client is never closed by the SDK.
    * ``trace_provider`` returns the caller's current W3C ``traceparent`` (for example from an
      OpenTelemetry context) so calls continue the caller's trace.
    """

    base_url: str
    token_provider: Callable[[], Any] | None = None
    connect_timeout: float = 5.0
    timeout: float = 30.0
    verify: bool | str | ssl.SSLContext = True
    headers: Mapping[str, str] = field(default_factory=dict)
    user_agent_suffix: str | None = None
    max_response_bytes: int = 8 * 1024 * 1024
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    auto_request_id: bool = True
    auto_traceparent: bool = True
    trace_provider: Callable[[], str | None] | None = None
    http_client: Any = None
    transport: Any = None

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if self.verify is False:
            raise ValueError(
                "disabling TLS verification is not supported; pass a CA bundle path or an ssl.SSLContext"
            )
        if self.connect_timeout <= 0 or self.timeout <= 0:
            raise ValueError("timeouts must be positive (unbounded requests are not allowed)")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        for name in self.headers:
            if name.lower() in RESERVED_HEADERS:
                raise ValueError(f"header {name!r} is managed by the SDK and cannot be overridden")

    def __repr__(self) -> str:
        return (
            f"Config(base_url={self.base_url!r}, token_provider="
            f"{'<set>' if self.token_provider else None}, timeout={self.timeout}, "
            f"headers=<{len(self.headers)} custom>)"
        )
