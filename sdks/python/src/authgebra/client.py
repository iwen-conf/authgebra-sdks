"""Synchronous client."""

from __future__ import annotations

import logging
import time
from types import TracebackType
from typing import Any, Iterator, Mapping

import httpx

from . import _core, _parse
from ._generated.sync import AdminClient
from .config import Config
from .errors import (
    ApiError,
    PaginationError,
    ProtocolError,
    TransportError,
)
from .models import (
    ApiResult,
    BatchCheckRequest,
    BatchCheckResult,
    CheckRequest,
    CheckResult,
    ExplainRequest,
    ExplainResult,
    ListResourcesRequest,
    ResourcePage,
    RevisionResult,
)

log = logging.getLogger("authgebra")

#: Upper bound for the paging iterator; reaching it raises instead of looping on.
DEFAULT_MAX_PAGES = 100


class AuthorizationClient:
    """Decision endpoints. ``deny`` is a normal result; ``NOT_READY`` is an error, never a deny.

    The decision endpoints are POSTs but pure reads (no mutation), so they follow the read retry rules.
    """

    def __init__(self, client: Client) -> None:
        self._c = client

    def check(
        self, request: CheckRequest, *, request_id: str | None = None, traceparent: str | None = None
    ) -> CheckResult:
        return _parse.check(self._post("/api/v1/authz/check", request.to_json(), request_id, traceparent))

    def batch_check(
        self, request: BatchCheckRequest, *, request_id: str | None = None, traceparent: str | None = None
    ) -> BatchCheckResult:
        return _parse.batch(
            self._post("/api/v1/authz/batch-check", request.to_json(), request_id, traceparent)
        )

    def explain(
        self, request: ExplainRequest, *, request_id: str | None = None, traceparent: str | None = None
    ) -> ExplainResult:
        return _parse.explain(
            self._post("/api/v1/authz/explain", request.to_json(), request_id, traceparent)
        )

    def list_resources(
        self,
        request: ListResourcesRequest,
        *,
        request_id: str | None = None,
        traceparent: str | None = None,
    ) -> ResourcePage:
        """One page. Pass ``page.next_cursor`` back via ``request.with_cursor`` for the next one."""
        return _parse.resource_page(
            self._post("/api/v1/authz/list-resources", request.to_json(), request_id, traceparent)
        )

    def iter_list_resources(
        self,
        request: ListResourcesRequest,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        traceparent: str | None = None,
    ) -> Iterator[ResourcePage]:
        """Yield pages lazily (backpressure: nothing is fetched until you ask for the next page).

        ``CURSOR_STALE`` propagates as :class:`~authgebra.CursorStaleError`; the SDK never restarts
        the listing on its own.  Pages from different snapshot generations are never stitched
        together: a generation change raises :class:`~authgebra.PaginationError`.
        """
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        trace = _core.resolve_traceparent(self._c.config, traceparent)
        current = request
        generation: int | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            page = self.list_resources(current, traceparent=trace)
            gen = page.meta.snapshot_generation if page.meta else None
            if generation is None:
                generation = gen
            elif gen is not None and gen != generation:
                raise PaginationError(
                    f"snapshot generation changed during paging ({generation} -> {gen}); restart explicitly"
                )
            yield page
            cursor = page.page.next_cursor
            if not page.page.has_more or cursor is None:
                return
            if cursor in seen:
                raise PaginationError("server returned a repeated cursor; refusing to loop")
            seen.add(cursor)
            current = request.with_cursor(cursor)
        raise PaginationError(f"page limit of {max_pages} reached while more pages remain")

    def revision(
        self, *, request_id: str | None = None, traceparent: str | None = None
    ) -> RevisionResult:
        return _parse.revision(
            self._c.request(
                "GET", "/api/v1/authz/revision", request_id=request_id, traceparent=traceparent
            )
        )

    def _post(
        self, path: str, body: Mapping[str, Any], request_id: str | None, traceparent: str | None
    ) -> ApiResult:
        return self._c.request(
            "POST", path, json=body, safe=True, request_id=request_id, traceparent=traceparent
        )


class Client:
    """Synchronous Authgebra client.

    >>> with Client(Config(base_url="https://authgebra.example", token_provider=lambda: token)) as c:
    ...     c.authorization.check(CheckRequest(subject_id, "document:read"))   # doctest: +SKIP
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._owns_http = config.http_client is None
        self._http: httpx.Client = config.http_client or httpx.Client(
            verify=config.verify,
            transport=config.transport,
            follow_redirects=False,
            trust_env=False,
        )
        self.authorization = AuthorizationClient(self)
        self.admin = AdminClient(self)

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # ------------------------------------------------------------------ low level

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        json: Any = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        traceparent: str | None = None,
        safe: bool | None = None,
        envelope: bool = True,
    ) -> ApiResult:
        """Send one logical request (with bounded retries) and interpret the response.

        ``safe`` defaults to ``True`` for GET/HEAD only.  Writes are not retried unless
        ``config.retry.retry_writes`` is set *and* ``idempotency_key`` is given.
        """
        cfg = self.config
        is_safe = method.upper() in ("GET", "HEAD") if safe is None else safe
        body = None if json is None else _core.serialize_json(json)
        trace = _core.resolve_traceparent(cfg, traceparent)
        retries_done = 0
        while True:
            attempt_trace = trace if trace is None or retries_done == 0 else _core.child_traceparent(trace)
            prep = _core.prepare(
                cfg,
                method,
                path,
                query=query,
                body=body,
                token=self._token(),
                if_match=if_match,
                idempotency_key=idempotency_key,
                request_id=request_id,
                traceparent=attempt_trace,
                safe=is_safe,
            )
            try:
                return _core.interpret(self._send(prep), envelope=envelope)
            except (ApiError, TransportError, ProtocolError) as exc:
                exc.attempts = retries_done + 1
                if not _core.should_retry(
                    cfg.retry,
                    safe=prep.safe,
                    has_key=prep.has_key,
                    retries_done=retries_done,
                    error=exc,
                ):
                    raise
                delay = _core.retry_delay(cfg.retry, retries_done, exc)
                log.debug("retrying %s %s in %.3fs (attempt %d)", prep.method, path, delay, retries_done + 2)
                (cfg.retry.sleep or time.sleep)(delay)
                retries_done += 1

    def _token(self) -> str | None:
        provider = self.config.token_provider
        if provider is None:
            return None
        token = provider()
        if token is not None and not isinstance(token, str):
            raise TypeError("token_provider must return str or None (use AsyncClient for async providers)")
        return token

    def _send(self, prep: _core.Prepared) -> _core.RawResponse:
        cfg = self.config
        deadline = time.monotonic() + cfg.timeout
        remaining = cfg.timeout
        timeout = httpx.Timeout(
            connect=min(cfg.connect_timeout, remaining), read=remaining, write=remaining, pool=remaining
        )
        try:
            with self._http.stream(
                prep.method,
                prep.url,
                params=prep.params or None,
                content=prep.content,
                headers=prep.headers,
                timeout=timeout,
            ) as response:
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > cfg.max_response_bytes:
                    raise ProtocolError("response_too_large", status=response.status_code, request_id=prep.request_id)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > cfg.max_response_bytes:
                        raise ProtocolError(
                            "response_too_large", status=response.status_code, request_id=prep.request_id
                        )
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise _core.build_transport_error("timeout", prep, "deadline exceeded")
                headers = {name.lower(): value for name, value in response.headers.items()}
                log.debug("%s %s -> %d", prep.method, prep.url.split("?", 1)[0], response.status_code)
                return _core.RawResponse(response.status_code, headers, b"".join(chunks))
        except (ProtocolError, TransportError):
            raise
        except httpx.HTTPError as exc:
            raise _core.transport_error(exc, prep) from exc
