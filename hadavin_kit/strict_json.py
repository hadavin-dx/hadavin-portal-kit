"""Strict JSON loading for every file the kit trusts: no NaN/Infinity (also via 1e999), no duplicate keys, objects only
where the caller says so. Python's default loader accepts all of these silently and JS disagrees on them."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


def _float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def loads(text: str) -> Any:
    return json.loads(text, parse_constant=_constant, parse_float=_float, object_pairs_hook=_pairs)


def load_object(path: Path | str) -> dict[str, Any]:
    data = loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a JSON object is required")  # noqa: TRY004
    return data
