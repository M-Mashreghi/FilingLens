"""Type-driven JSON codec for frozen domain dataclasses."""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from datetime import date, datetime
from enum import Enum
from functools import cache
from typing import Any, TypeVar

T = TypeVar("T")


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))


@cache
def _hints(cls: Any) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _decode(tp: Any, raw: Any) -> Any:
    if raw is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _decode(non_none[0], raw)
        for candidate in non_none:  # first one that decodes cleanly
            try:
                return _decode(candidate, raw)
            except (TypeError, ValueError):
                continue
        return raw
    if origin is tuple:
        item_t = args[0] if args else Any
        return tuple(_decode(item_t, v) for v in raw)
    if origin is list:
        return [_decode(args[0] if args else Any, v) for v in raw]
    if origin is dict:
        val_t = args[1] if len(args) == 2 else Any
        return {k: _decode(val_t, v) for k, v in raw.items()}
    if tp is Any:
        return raw
    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp):
            return from_jsonable(tp, raw)
        if issubclass(tp, Enum):
            return tp(raw)
        if tp is datetime:
            return datetime.fromisoformat(raw)
        if tp is date:
            return date.fromisoformat(raw)
        if tp is float and isinstance(raw, int):
            return float(raw)
    return raw


def from_jsonable(cls: type[T], data: dict[str, Any]) -> T:
    hints = _hints(typing.cast(Any, cls))
    kwargs = {
        f.name: _decode(hints[f.name], data[f.name])
        for f in dataclasses.fields(cls)  # type: ignore[arg-type]
        if f.init and f.name in data
    }
    return cls(**kwargs)


def loads(cls: type[T], text: str) -> T:
    return from_jsonable(cls, json.loads(text))
