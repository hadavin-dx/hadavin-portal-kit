"""Helpers shared by the kit's tests and by every demo's conformance tests (`from hadavin_kit import testing`)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hadavin_kit import chain, strict_json, validate

KIT = Path(__file__).resolve().parents[1]
SLUGS = ("refunds", "apexc", "intake", "dealdesk", "askdata")


def example(slug: str) -> dict[str, Any]:
    """A fresh copy of the minor-1 example export of one demo."""
    return copy.deepcopy(strict_json.load_object(KIT / "demo_only" / "examples" / f"{slug}.json"))


def compat(slug: str) -> dict[str, Any]:
    """A fresh copy of the frozen minor-0 export (the shape published first)."""
    return copy.deepcopy(
        strict_json.load_object(KIT / "demo_only" / "fixtures" / "compat-v1.0" / f"{slug}.json")
    )


def codes(doc: Any) -> set[str]:
    return {p.code for p in validate.validate(doc)}


def messages(doc: Any) -> list[str]:
    return [str(p) for p in validate.validate(doc)]


def mutated(
    slug: str, fn: Callable[[dict[str, Any]], None], *, reseal: bool = False
) -> dict[str, Any]:
    d = example(slug)
    fn(d)
    if reseal:
        chain.reseal(d)
    return d


def case(doc: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(c for c in doc["cases"] if c["case_id"] == case_id)
