"""Typed parsing of the authorization endpoints' ``data`` payloads (shared by sync and async)."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ProtocolError
from ._generated import models as api_models
from .models import (
    ApiResult,
    BatchCheckResult,
    BatchDecision,
    CheckResult,
    Decision,
    ExplainResult,
    ResourcePage,
    ResourceRef,
    RevisionResult,
)


def _bad(result: ApiResult, what: str) -> ProtocolError:
    return ProtocolError(
        "malformed_success_body",
        status=result.info.status,
        request_id=result.info.request_id,
        trace_id=result.info.trace_id,
        excerpt=what,
    )


def _decision(result: ApiResult, value: Any) -> Decision:
    try:
        return Decision(value)
    except ValueError:
        # Fail closed: an unrecognised decision is never interpreted as allow (or deny).
        raise ProtocolError(
            "unknown_decision",
            status=result.info.status,
            request_id=result.info.request_id,
            trace_id=result.info.trace_id,
        ) from None


def _obj(result: ApiResult, value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _bad(result, what)
    return value


def _str(result: ApiResult, obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise _bad(result, f"{key} must be a string")
    return value


def _int(result: ApiResult, obj: Mapping[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(result, f"{key} must be an integer")
    return value


def _policy(result: ApiResult, obj: Mapping[str, Any]) -> api_models.AuthzPolicyRef | None:
    value = obj.get("matched_policy")
    if value is None:
        return None
    try:
        return api_models.AuthzPolicyRef.from_json(value)
    except ValueError:
        raise _bad(result, "matched_policy") from None


def resource_ref(result: ApiResult, value: Any) -> ResourceRef:
    obj = _obj(result, value, "resource")
    return ResourceRef(_str(result, obj, "resource_type_code"), _str(result, obj, "external_id"))


def check(result: ApiResult) -> CheckResult:
    data = _obj(result, result.data, "data")
    return CheckResult(
        decision=_decision(result, data.get("decision")),
        reason_code=_str(result, data, "reason_code"),
        matched_policy=_policy(result, data),
        info=result.info,
        meta=result.meta,
    )


def _batch_items(result: ApiResult, value: Any) -> tuple[BatchDecision, ...]:
    if not isinstance(value, list):
        raise _bad(result, "decision list expected")
    items = []
    for entry in value:
        obj = _obj(result, entry, "batch item")
        items.append(
            BatchDecision(
                resource=resource_ref(result, obj.get("resource")),
                reason_code=_str(result, obj, "reason_code"),
                matched_policy=_policy(result, obj),
            )
        )
    return tuple(items)


def batch(result: ApiResult) -> BatchCheckResult:
    data = _obj(result, result.data, "data")
    return BatchCheckResult(
        allowed=_batch_items(result, data.get("allowed")),
        denied=_batch_items(result, data.get("denied")),
        info=result.info,
        meta=result.meta,
    )


def resource_page(result: ApiResult) -> ResourcePage:
    if not isinstance(result.data, list) or result.page is None:
        raise _bad(result, "paged list expected")
    return ResourcePage(
        items=tuple(resource_ref(result, entry) for entry in result.data),
        page=result.page,
        info=result.info,
        meta=result.meta,
    )


def explain(result: ApiResult) -> ExplainResult:
    data = _obj(result, result.data, "data")
    policies = data.get("policies")
    truncated = data.get("truncated")
    if not isinstance(policies, list) or not isinstance(truncated, bool):
        raise _bad(result, "policies/truncated")
    try:
        explanations = tuple(api_models.AuthzPolicyExplanation.from_json(policy) for policy in policies)
    except ValueError:
        raise _bad(result, "policies") from None
    return ExplainResult(
        decision=_decision(result, data.get("decision")),
        reason_code=_str(result, data, "reason_code"),
        matched_policy=_policy(result, data),
        policies=explanations,
        truncated=truncated,
        total_steps=_int(result, data, "total_steps"),
        info=result.info,
        meta=result.meta,
    )


def revision(result: ApiResult) -> RevisionResult:
    data = _obj(result, result.data, "data")
    return RevisionResult(
        db_revision=_int(result, data, "db_revision"),
        snapshot_generation=_int(result, data, "snapshot_generation"),
        status=_str(result, data, "status"),
        info=result.info,
        meta=result.meta,
    )
