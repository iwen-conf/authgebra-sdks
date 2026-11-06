"""Transport-independent protocol core shared by the sync and async clients."""

from __future__ import annotations

import json
import random
import re
import secrets
import ssl
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import quote

import httpx

from ._generated.codes import KNOWN_ERROR_CODES
from .config import Config
from .errors import (
    API_ERROR_CLASSES,
    ApiError,
    AuthgebraError,
    CommitState,
    ProtocolError,
    TransportError,
    Violation,
)
from .models import ApiResult, Meta, PageInfo, ResponseInfo, RetryPolicy

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEX = re.compile(r"^[0-9a-f]+$")

RETRYABLE_CODES = frozenset({"RATE_LIMITED", "NOT_READY", "IDEMPOTENCY_IN_PROGRESS"})
_EXCERPT = 1024


# --------------------------------------------------------------------------- ids / tracing


def valid_request_id(value: str | None) -> str | None:
    return value if value is not None and REQUEST_ID_RE.fullmatch(value) else None


def valid_trace_id(value: str | None) -> str | None:
    if value is not None and TRACE_ID_RE.fullmatch(value) and value != "0" * 32:
        return value
    return None


def new_request_id() -> str:
    return "py-" + uuid.uuid4().hex


def parse_traceparent(value: str) -> tuple[str, str, int] | None:
    """Strict W3C parse; ``None`` for anything invalid (uppercase, all-zero, ``ff`` ...)."""
    if len(value) < 55 or value[2] != "-" or value[35] != "-" or value[52] != "-":
        return None
    version, trace, span, flags = value[0:2], value[3:35], value[36:52], value[53:55]
    if not (_HEX.fullmatch(version) and _HEX.fullmatch(trace) and _HEX.fullmatch(span)):
        return None
    if not _HEX.fullmatch(flags) or version == "ff":
        return None
    if version == "00" and len(value) != 55:
        return None
    if version != "00" and len(value) > 55 and value[55] != "-":
        return None
    if trace == "0" * 32 or span == "0" * 16:
        return None
    return trace, span, int(flags, 16)


def new_traceparent(trace_id: str | None = None, sampled: bool = False) -> str:
    trace = trace_id or secrets.token_hex(16)
    while trace == "0" * 32:  # pragma: no cover - 2**-128
        trace = secrets.token_hex(16)
    span = secrets.token_hex(8)
    while span == "0" * 16:  # pragma: no cover - 2**-64
        span = secrets.token_hex(8)
    return f"00-{trace}-{span}-{'01' if sampled else '00'}"


def child_traceparent(traceparent: str) -> str:
    """Same trace, new span id (each attempt is its own span)."""
    parsed = parse_traceparent(traceparent)
    if parsed is None:
        return new_traceparent()
    return new_traceparent(parsed[0], bool(parsed[2] & 1))


def resolve_traceparent(config: Config, explicit: str | None) -> str | None:
    for candidate in (explicit, config.trace_provider() if config.trace_provider else None):
        if candidate is not None and parse_traceparent(candidate) is not None:
            return candidate
    return new_traceparent() if config.auto_traceparent else None


def validate_idempotency_key(key: str | None) -> str | None:
    if key is None:
        return None
    stripped = key.strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(stripped):
        raise ValueError("Idempotency-Key must be 1-128 chars of [A-Za-z0-9._-]")
    return stripped


def new_idempotency_key() -> str:
    """Convenience: a fresh, valid idempotency key. Reuse it for every resend of the same write."""
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- request building


def path_with(template: str, **params: object) -> str:
    """Fill ``{name}`` segments with percent-encoded values."""
    out = template
    for name, value in params.items():
        out = out.replace("{" + name + "}", quote(str(value), safe=""))
    return out


def serialize_json(body: Any) -> bytes:
    """Compact UTF-8 JSON; non-ASCII is written as-is (no ``\\u`` escapes), NaN is rejected."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


QueryPairs = list[tuple[str, str | int | float | bool | None]]


def encode_query(query: Mapping[str, object] | None) -> QueryPairs:
    pairs: QueryPairs = []
    for name, value in (query or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        else:
            pairs.append((name, str(value)))
    return pairs


@dataclass
class Prepared:
    method: str
    url: str
    params: QueryPairs
    content: bytes | None
    headers: dict[str, str]
    safe: bool
    has_key: bool
    request_id: str | None


def prepare(
    config: Config,
    method: str,
    path: str,
    *,
    query: Mapping[str, object] | None,
    body: bytes | None,
    token: str | None,
    if_match: str | None,
    idempotency_key: str | None,
    request_id: str | None,
    traceparent: str | None,
    safe: bool,
) -> Prepared:
    from ._version import __version__

    headers: dict[str, str] = {name: value for name, value in config.headers.items()}
    ua = f"authgebra-python/{__version__}"
    if config.user_agent_suffix:
        ua += f" {config.user_agent_suffix}"
    headers["User-Agent"] = ua
    headers["Accept"] = "application/json"
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if if_match is not None:
        headers["If-Match"] = if_match
    key = validate_idempotency_key(idempotency_key)
    if key is not None:
        headers["Idempotency-Key"] = key
    sent_request_id = valid_request_id(request_id)
    if sent_request_id is None and config.auto_request_id:
        sent_request_id = new_request_id()
    if sent_request_id is not None:
        headers["X-Request-ID"] = sent_request_id
    if traceparent is not None:
        headers["traceparent"] = traceparent
    return Prepared(
        method=method.upper(),
        url=config.base_url.rstrip("/") + path,
        params=encode_query(query),
        content=body,
        headers=headers,
        safe=safe,
        has_key=key is not None,
        request_id=sent_request_id,
    )


# --------------------------------------------------------------------------- transport errors


def _ssl_in_chain(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, ssl.SSLError):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def transport_error(exc: BaseException, prep: Prepared) -> TransportError:
    if isinstance(exc, httpx.TimeoutException):
        kind = "timeout"
    elif isinstance(exc, httpx.ConnectError):
        kind = "tls_error" if _ssl_in_chain(exc) else "connect_error"
    elif isinstance(exc, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, httpx.CloseError)):
        kind = "connection_reset"
    else:
        kind = "transport"
    return build_transport_error(kind, prep, type(exc).__name__)


def build_transport_error(kind: str, prep: Prepared, detail: str = "") -> TransportError:
    # A failed write may or may not have been applied: never claim "not committed".
    state = None if prep.safe else CommitState.UNKNOWN
    return TransportError(kind, commit_state=state, request_id=prep.request_id, detail=detail)


# --------------------------------------------------------------------------- response parsing


@dataclass
class RawResponse:
    status: int
    headers: Mapping[str, str]  # lower-cased names
    body: bytes


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON number {name}")


def _excerpt(body: bytes) -> str:
    return body[:_EXCERPT].decode("utf-8", errors="replace")


def _correlation(headers: Mapping[str, str]) -> tuple[str | None, str | None]:
    header_rid = valid_request_id(headers.get("x-request-id"))
    trace = valid_trace_id(headers.get("x-trace-id"))
    if trace is None:
        tp = headers.get("traceparent")
        parsed = parse_traceparent(tp) if tp else None
        trace = parsed[0] if parsed else None
    return header_rid, trace


def _parse_meta(value: Any, raw: RawResponse, rid: str | None, trace: str | None) -> Meta | None:
    if value is None:
        return None

    def bad() -> ProtocolError:
        return ProtocolError(
            "malformed_success_body", status=raw.status, request_id=rid, trace_id=trace,
            excerpt=_excerpt(raw.body),
        )

    if not isinstance(value, dict):
        raise bad()
    revision = value.get("authz_revision")
    generation = value.get("snapshot_generation")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise bad()
    if generation is not None and (isinstance(generation, bool) or not isinstance(generation, int)):
        raise bad()
    return Meta(authz_revision=revision, snapshot_generation=generation)


def _parse_page(value: Any, raw: RawResponse, rid: str | None, trace: str | None) -> PageInfo | None:
    if value is None:
        return None
    if (
        isinstance(value, dict)
        and isinstance(value.get("has_more"), bool)
        and (value.get("next_cursor") is None or isinstance(value.get("next_cursor"), str))
    ):
        return PageInfo(next_cursor=value.get("next_cursor"), has_more=value["has_more"])
    raise ProtocolError(
        "malformed_success_body", status=raw.status, request_id=rid, trace_id=trace,
        excerpt=_excerpt(raw.body),
    )


def _load(raw: RawResponse, rid: str | None, trace: str | None) -> Any:
    try:
        return json.loads(raw.body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(
            "non_json_response", status=raw.status, request_id=rid, trace_id=trace,
            excerpt=_excerpt(raw.body),
        ) from exc


def interpret(raw: RawResponse, *, envelope: bool = True) -> ApiResult:
    """Turn one HTTP response into a result, or raise ``ApiError`` / ``ProtocolError``."""
    header_rid, header_trace = _correlation(raw.headers)
    if 200 <= raw.status < 300:
        return _success(raw, envelope, header_rid, header_trace)
    raise _error(raw, header_rid, header_trace)


def _success(raw: RawResponse, envelope: bool, rid: str | None, trace: str | None) -> ApiResult:
    info = ResponseInfo(status=raw.status, request_id=rid, trace_id=trace)
    etag = raw.headers.get("etag")
    replayed = raw.headers.get("idempotency-replayed", "").lower() == "true"
    revision_header: int | None = None
    header_value = raw.headers.get("x-authz-revision")
    if header_value is not None and header_value.isdigit():
        revision_header = int(header_value)
    if raw.status == 204 and not raw.body:
        return ApiResult(None, info, None, None, etag, replayed, revision_header)
    payload = _load(raw, rid, trace)
    if not envelope:
        return ApiResult(payload, info, None, None, etag, replayed, revision_header)
    if not isinstance(payload, dict) or "data" not in payload:
        raise ProtocolError(
            "malformed_success_body", status=raw.status, request_id=rid, trace_id=trace,
            excerpt=_excerpt(raw.body),
        )
    return ApiResult(
        data=payload["data"],
        info=info,
        meta=_parse_meta(payload.get("meta"), raw, rid, trace),
        page=_parse_page(payload.get("page"), raw, rid, trace),
        etag=etag,
        replayed=replayed,
        authz_revision_header=revision_header,
    )


def _error(raw: RawResponse, header_rid: str | None, header_trace: str | None) -> AuthgebraError:
    payload = _load(raw, header_rid, header_trace)
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if not isinstance(err, dict) or not isinstance(code, str) or not code:
        return ProtocolError(
            "malformed_error_body", status=raw.status, request_id=header_rid,
            trace_id=header_trace, excerpt=_excerpt(raw.body),
        )
    raw_message = err.get("message")
    message: str = raw_message if isinstance(raw_message, str) else ""
    body_rid = valid_request_id(err.get("request_id") if isinstance(err.get("request_id"), str) else None)
    request_id = header_rid or body_rid
    mismatch = header_rid is not None and body_rid is not None and header_rid != body_rid
    trace_value = err.get("trace_id")
    trace = valid_trace_id(trace_value if isinstance(trace_value, str) else None) or header_trace

    details = err.get("details") if isinstance(err.get("details"), dict) else None
    violations: list[Violation] = []
    if details is not None and isinstance(details.get("violations"), list):
        for item in details["violations"]:
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                path = item.get("path")
                text = item.get("message")
                violations.append(
                    Violation(
                        path=path if isinstance(path, str) else None,
                        reason=item["reason"],
                        message=text if isinstance(text, str) else "",
                    )
                )

    retryable = err.get("retryable") if isinstance(err.get("retryable"), bool) else None
    retry_after: int | None = None
    header_retry = raw.headers.get("retry-after", "").strip()
    body_retry = err.get("retry_after_seconds")
    if header_retry.isdigit():
        retry_after = int(header_retry)
    elif isinstance(body_retry, int) and not isinstance(body_retry, bool) and body_retry >= 0:
        retry_after = body_retry

    commit: CommitState | None = None
    commit_value = err.get("commit_state")
    if isinstance(commit_value, str):
        try:
            commit = CommitState(commit_value)
        except ValueError:
            commit = CommitState.UNKNOWN

    cls = API_ERROR_CLASSES.get(code, ApiError)
    return cls(
        status=raw.status,
        code=code,
        known_code=code in KNOWN_ERROR_CODES,
        message=message,
        request_id=request_id,
        body_request_id=body_rid,
        request_id_mismatch=mismatch,
        request_id_header_valid=header_rid is not None,
        trace_id=trace,
        details=details,
        violations=tuple(violations),
        retryable=retryable,
        retry_after_seconds=retry_after,
        commit_state=commit,
        raw_body_excerpt=_excerpt(raw.body),
    )


# --------------------------------------------------------------------------- retry policy


def should_retry(
    policy: RetryPolicy, *, safe: bool, has_key: bool, retries_done: int, error: BaseException
) -> bool:
    if retries_done >= policy.max_retries:
        return False
    if not safe and not (policy.retry_writes and has_key):
        return False
    if isinstance(error, ApiError):
        if error.commit_state is CommitState.COMMITTED_UNPUBLISHED:
            return False
        return error.code in RETRYABLE_CODES
    if isinstance(error, TransportError):
        return error.error != "tls_error"
    return False


def retry_delay(policy: RetryPolicy, retries_done: int, error: BaseException) -> float:
    if isinstance(error, ApiError) and error.retry_after_seconds is not None:
        return float(min(float(error.retry_after_seconds), policy.max_delay))
    base = min(policy.base_delay * (2**retries_done), policy.max_delay)
    return float(min(base * (1.0 + policy.jitter * random.random()), policy.max_delay))


# --------------------------------------------------------------------------- requester protocols


class SyncRequester(Protocol):
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
    ) -> ApiResult: ...


class AsyncRequester(Protocol):
    async def request(
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
    ) -> ApiResult: ...
