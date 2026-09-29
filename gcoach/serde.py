"""Tiny type-hint driven (de)serializer for the dataclass models.

Dataclasses are used instead of pydantic for the simulation models because the
planner clones thousands of states per recommendation; this module keeps them
JSON-serializable without paying validation cost on every copy.
"""
from __future__ import annotations

import dataclasses
import types
import typing
from enum import Enum
from functools import lru_cache
from typing import Any, Union, get_args, get_origin, get_type_hints


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {(k.value if isinstance(k, Enum) else k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_dict(v) for v in obj]
    return obj


@lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def from_dict(tp: Any, data: Any) -> Any:
    if data is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        last_error: Exception | None = None
        for arg in args:
            try:
                return from_dict(arg, data)
            except (TypeError, ValueError, KeyError) as exc:
                last_error = exc
        raise ValueError(f"cannot parse {data!r} as {tp}: {last_error}")
    if origin in (list, typing.List, tuple, set):
        (arg,) = get_args(tp)[:1] or (Any,)
        return [from_dict(arg, v) for v in data]
    if origin in (dict, typing.Dict):
        kt, vt = get_args(tp) or (Any, Any)
        return {from_dict(kt, k): from_dict(vt, v) for k, v in data.items()}
    if tp is Any:
        return data
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(data)
    if dataclasses.is_dataclass(tp):
        hints = _hints(tp)
        kwargs = {}
        for f in dataclasses.fields(tp):
            if f.name in data:
                kwargs[f.name] = from_dict(hints[f.name], data[f.name])
        return tp(**kwargs)
    if tp is float and isinstance(data, int):
        return float(data)
    return data
