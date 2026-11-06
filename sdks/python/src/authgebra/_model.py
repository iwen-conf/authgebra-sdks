"""JSON model codec shared by generated synchronous and asynchronous operations."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import fields
from enum import Enum
from typing import ClassVar, TypeVar

from .errors import ProtocolError
from .models import ApiResult, JSONValue as JSONValue


class Unset(Enum):
    """A field absent from JSON; distinct from explicit JSON null."""

    TOKEN = 0


UNSET = Unset.TOKEN


class StringEnum(str, Enum):
    """Named wire enum that preserves a newer server's unknown string values."""

    @classmethod
    def _missing_(cls, value: object) -> StringEnum | None:
        if not isinstance(value, str):
            return None
        member = str.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member


class Model(Mapping[str, object]):
    """A generated named object; mapping access preserves existing dict consumers."""

    __schema_pointer__: ClassVar[str]
    __json_fields__: ClassVar[dict[str, str]]
    __request_model__: ClassVar[bool] = False

    def __getitem__(self, key: str) -> object:
        name = self.__json_fields__.get(key)
        if name is None:
            raise KeyError(key)
        value: object = getattr(self, name)
        if isinstance(value, Unset):
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[str]:
        return (key for key, name in self.__json_fields__.items() if not isinstance(getattr(self, name), Unset))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def to_json(self) -> dict[str, JSONValue]:
        return {key: json_value(self[key]) for key in self}

    @classmethod
    def from_json(cls: type[M], value: object) -> M:
        parsed = decode(cls.__schema_pointer__, value, request=cls.__request_model__)
        if not isinstance(parsed, cls):
            raise ValueError(f"expected {cls.__name__}")
        return parsed


M = TypeVar("M", bound=Model)
R = TypeVar("R", bound=ApiResult)


def json_value(value: object) -> JSONValue:
    """Convert only JSON values and named models, without a lossy floating-point hop."""
    if isinstance(value, Unset):
        raise ValueError("UNSET is only valid as an omitted object field")
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, Model):
        return value.to_json()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            result[key] = json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def request_body(value: Model | Mapping[str, object] | None) -> JSONValue:
    # The legacy dict path remains server-validated; generated model inputs validate locally.
    if isinstance(value, Model):
        raw = value.to_json()
        decode(value.__schema_pointer__, raw, request=True)
        return raw
    return json_value(value)


def _schema(pointer: str) -> dict[str, object]:
    from ._generated.models import SCHEMAS

    return SCHEMAS[pointer]


def _child(pointer: str, value: object, *, request: bool) -> object:
    return decode(pointer, value, request=request)


def decode(pointer: str, value: object, *, request: bool = False) -> object:
    """Decode one schema location. Unknown response properties remain forward compatible."""
    from ._generated.models import MODEL_TYPES, ENUM_TYPES

    schema = _schema(pointer)
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return decode(ref, value, request=request)
    for keyword in ("oneOf", "anyOf", "allOf"):
        arms = schema.get(keyword)
        if isinstance(arms, list):
            if keyword == "allOf":
                if len(arms) != 1:
                    raise ValueError(f"unsupported allOf at {pointer}")
                return _child(f"{pointer}/{keyword}/0", value, request=request)
            successes: list[object] = []
            for index in range(len(arms)):
                try:
                    successes.append(_child(f"{pointer}/{keyword}/{index}", value, request=request))
                except ValueError:
                    pass
            if not successes or (keyword == "oneOf" and len(successes) != 1):
                raise ValueError(f"value does not match {keyword} at {pointer}")
            return successes[0]
    kind = schema.get("type")
    if isinstance(kind, list):
        if value is None and "null" in kind:
            return None
        kind = next((item for item in kind if item != "null"), None)
    if value is None:
        if kind == "null" or kind is None:
            return None
        raise ValueError(f"null is not accepted at {pointer}")
    enum = schema.get("enum")
    if isinstance(enum, list):
        if value not in enum and (request or len(enum) == 1):
            raise ValueError(f"unknown enum value at {pointer}")
        enum_type = ENUM_TYPES.get(pointer)
        return enum_type(value) if enum_type is not None else value
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"string required at {pointer}")
        if request:
            minimum, maximum = schema.get("minLength"), schema.get("maxLength")
            if isinstance(minimum, int) and len(value) < minimum:
                raise ValueError(f"string too short at {pointer}")
            if isinstance(maximum, int) and len(value) > maximum:
                raise ValueError(f"string too long at {pointer}")
            pattern = schema.get("pattern")
            if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                raise ValueError(f"string pattern mismatch at {pointer}")
            if schema.get("format") == "uuid":
                try:
                    uuid.UUID(value)
                except ValueError:
                    raise ValueError(f"UUID required at {pointer}") from None
        return value
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and not isinstance(value, int)):
            raise ValueError(f"{kind} required at {pointer}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"finite number required at {pointer}")
        if request:
            minimum, maximum = schema.get("minimum"), schema.get("maximum")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise ValueError(f"number below minimum at {pointer}")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise ValueError(f"number above maximum at {pointer}")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"boolean required at {pointer}")
        return value
    if kind == "array":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"array required at {pointer}")
        if request:
            minimum, maximum = schema.get("minItems"), schema.get("maxItems")
            if isinstance(minimum, int) and len(value) < minimum:
                raise ValueError(f"array too short at {pointer}")
            if isinstance(maximum, int) and len(value) > maximum:
                raise ValueError(f"array too long at {pointer}")
            if schema.get("uniqueItems") is True:
                import json
                canonical = [json.dumps(json_value(item), sort_keys=True) for item in value]
                if len(set(canonical)) != len(canonical):
                    raise ValueError(f"duplicate array items at {pointer}")
        return [_child(f"{pointer}/items", item, request=request) for item in value]
    if kind == "object" or "properties" in schema:
        if not isinstance(value, Mapping):
            raise ValueError(f"object required at {pointer}")
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"string object keys required at {pointer}")
        props = schema.get("properties")
        if isinstance(props, dict):
            required = schema.get("required", [])
            if not isinstance(required, list):
                raise ValueError(f"invalid required metadata at {pointer}")
            if any(key not in value for key in required):
                raise ValueError(f"required field absent at {pointer}")
            if request and schema.get("additionalProperties") is False and any(key not in props for key in value):
                raise ValueError(f"unknown field at {pointer}")
            cls = MODEL_TYPES[pointer]
            params: dict[str, object] = {}
            for key, name in cls.__json_fields__.items():
                if key in value:
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    params[name] = _child(f"{pointer}/properties/{escaped}", value[key], request=request)
            return cls(**params)
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return {key: _child(f"{pointer}/additionalProperties", item, request=request) for key, item in value.items()}
        return json_value(value)
    if kind == "null":
        raise ValueError(f"null required at {pointer}")
    if kind is None:
        return json_value(value)
    raise ValueError(f"unsupported schema type at {pointer}: {kind}")


def typed_result(result: ApiResult, cls: type[R], pointer: str) -> R:
    try:
        data = decode(pointer, result.data)
    except ValueError as exc:
        raise ProtocolError(
            "malformed_success_body", status=result.info.status,
            request_id=result.info.request_id, trace_id=result.info.trace_id,
            excerpt=str(exc),
        ) from None
    values = {field.name: getattr(result, field.name) for field in fields(ApiResult)}
    values["data"] = data
    return cls(**values)
