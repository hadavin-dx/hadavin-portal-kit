"""The one strict validator of `hadavin.demo-export/1`: schema, minor, cross-field rules and chain, with typed errors.

`validate(doc)` never raises on a bad export; it returns `Problem`s, each with a `code` from `problems.CODES`:
UNKNOWN_KEY (a key the schema does not have), WRONG_TYPE, MISSING_KEY, BAD_VALUE (enum, pattern, range),
OVERSIZE, CONFUSABLE_ID (a non-ASCII or case-colliding identifier), BAD_CHARACTER (control, bidi, zero-width),
UNSUPPORTED_MINOR / MINOR_TOO_LOW, CHAIN, RULE (a cross-field rule). Rule checks run only when the schema passed
(they index into the document), so a malformed export reports its shape problems first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from hadavin_kit import export_rules, paths, rules_v11, strict_json, versioning
from hadavin_kit.problems import CODES, code_of

SCHEMA_PATH = paths.SCHEMA


@dataclass(frozen=True)
class Problem:
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in CODES:
            raise ValueError(f"unknown problem code {self.code!r}")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}

    def __str__(self) -> str:
        return f"{self.code} {self.path or '<root>'}: {self.message}"


def schema() -> dict[str, Any]:
    return json.loads(paths.require(SCHEMA_PATH).read_text(encoding="utf-8"))


def _path(err: ValidationError) -> str:
    return "/".join(ascii(str(p))[1:-1] for p in err.absolute_path)


def _classify(err: ValidationError) -> Problem:
    v, path = err.validator, _path(err)
    if v == "additionalProperties":
        extra = sorted(set(err.instance) - set(err.schema.get("properties", {})))
        return Problem("UNKNOWN_KEY", path, f"unknown key(s) {[str(k)[:40] for k in extra][:5]}")
    if v == "required":
        return Problem("MISSING_KEY", path, err.message[:160])
    if v == "type":
        return Problem("WRONG_TYPE", path, err.message[:160])
    if v in ("maxLength", "maxItems", "maxProperties"):
        return Problem("OVERSIZE", path, f"{v} exceeded")
    if v == "pattern" and isinstance(err.instance, str) and not err.instance.isascii():
        return Problem("CONFUSABLE_ID", path, f"non-ASCII identifier {err.instance[:40]!r}")
    return Problem("BAD_VALUE", path, err.message[:160])


def _precheck(doc: dict[str, Any]) -> list[Problem]:
    """Cheap size limits BEFORE the schema walks every item: a document one item over a limit must not cost a minute."""
    sch = schema()
    out = [
        Problem("OVERSIZE", "", str(m)) for m in rules_v11.limits(doc, export_rules.MAX_DOC_BYTES)
    ]
    lists = {
        "cases": doc.get("cases"),
        "approvals": doc.get("approvals"),
        "adapter_calls": doc.get("adapter_calls"),
    }
    audit = doc.get("audit")
    if isinstance(audit, dict):
        lists["audit/events"] = audit.get("events")
    for path, value in lists.items():
        node = sch["properties"]
        for part in path.split("/"):
            node = node[part] if part in node else node["properties"][part]
        limit = node.get("maxItems")
        if isinstance(value, list) and limit is not None and len(value) > limit:
            out.append(Problem("OVERSIZE", path, f"{len(value)} items; the limit is {limit}"))
    return out


def shape_problems(doc: Any) -> list[Problem]:
    if not isinstance(doc, dict):
        return [Problem("WRONG_TYPE", "", "an export must be a JSON object")]
    if early := _precheck(doc):
        return early
    errs = sorted(
        Draft202012Validator(schema()).iter_errors(doc), key=lambda e: list(map(str, e.path))
    )
    return [_classify(e) for e in errs]


def validate(doc: Any) -> list[Problem]:
    """Every problem of `doc`; an empty list means the export is valid (it may still be a consistent lie)."""
    problems = shape_problems(doc)
    if problems:
        # the minor check needs only the root, so it is still useful next to shape errors
        if isinstance(doc, dict):
            try:
                versioning.declared_minor(doc)
            except versioning.UnsupportedMinor as err:
                problems.append(Problem("UNSUPPORTED_MINOR", "schema_minor", str(err)))
        return problems
    try:
        found = export_rules.check(doc)
    except RecursionError:
        return [Problem("OVERSIZE", "", "nesting too deep")]
    return [Problem(code_of(p), "", str(p)) for p in found]


def load_and_validate(path: Path | str) -> tuple[dict[str, Any] | None, list[Problem]]:
    """Strict-load (no NaN, no duplicate keys, an object) then validate. Load failures are NOT_JSON problems."""
    try:
        doc = strict_json.load_object(path)
    except (OSError, ValueError, RecursionError) as err:
        return None, [Problem("NOT_JSON", "", f"{type(err).__name__}: {str(err)[:160]}")]
    return doc, validate(doc)
