"""Versioning of `hadavin.demo-export/1`: additive minors.

Rules (the migration contract, tested in `tests/test_versioning.py`):
1. The `schema` string stays `hadavin.demo-export/1` for every minor. A reader that knows minor N reads every export
   of minor 0..N. An export that declares a minor above the reader's `MAX_MINOR` is refused (`UNSUPPORTED_MINOR`).
2. `schema_minor` is optional; absent means 0 (the shape first published on the branch, frozen copy in
   the frozen compat fixtures). Every export in that frozen set keeps validating.
3. A minor ADDS: optional keys, enum values, a new chain format. It does not remove or retype a key, make an old key
   required or narrow an enum. `additive_diff` checks that against the frozen copy (it also flags raised bounds, changed
   patterns, tightened additionalProperties and any combinator it cannot prove only loosens). The reviewed exceptions
   (five unprovable loosenings, three tightenings) are listed with reasons in `ACCEPTED_TIGHTENINGS`; nothing else passes.
4. A key or enum value introduced in minor N may appear only in an export that declares `schema_minor >= N`
   (`MINOR_TOO_LOW`). That is how an old reader's refusal is predictable instead of accidental.
5. Rules that need a new field (tenant on every record, honesty kind present, hash-chain/2) apply from the minor that
   introduced them. The general limits (document size, string length, nesting depth, unique hard-zero keys, the
   confident_on_abstain de-duplication) apply to every minor: the minor-0 shape existed only on the unmerged branch
   kit/portal-kit (no exporter outside it), so no published export could be invalidated; that is a choice, not a property
   of the mechanism.
6. `migrate(doc)` upgrades a minor-0 export to the latest minor ONLY when the data to do so honestly is present; it
   never invents a tenant, a terminal state or a measurement. It returns the problems it could not close.
7. STATUS: minor 1 is DRAFT until a real demo exporter passes `kitcheck` on its own output. Until then it may change
   incompatibly, announced in the pull request. A change that must break readers gets a new schema string (/2).
"""

from __future__ import annotations

from typing import Any

SCHEMA = "hadavin.demo-export/1"
MAX_MINOR = 1

# minor -> context -> keys that did not exist before that minor. Contexts are the object kinds of the schema.
KEYS_ADDED: dict[int, dict[str, tuple[str, ...]]] = {
    1: {
        "root": (
            "schema_minor",
            "tenant_id",
            "ledger",
            "amounts",
            "load_profile",
            "read_log",
            "provider_terms",
            "segregation",
        ),
        "case": ("tenant_id", "terminal_state", "receipt_id", "queue"),
        "expected": ("verdict", "denied_calls", "terminal_state"),
        "approval": (
            "tenant_id",
            "lifecycle",
            "plan_step",
            "excluded_user_ids",
            "bound",
        ),
        "approver": ("roles_at_decision", "sig_scheme"),
        "adapter_call": ("tenant_id", "ticket_id", "receipt_id"),
        "audit_event": ("tenant_id",),
        "scorecard": ("steered", "false_hold", "hard_zero_exposure"),
        "run": ("recorded_turns_sha256",),
    }
}

# minor -> (context, key) -> enum values that did not exist before that minor.
ENUMS_ADDED: dict[int, dict[tuple[str, str], tuple[str, ...]]] = {
    1: {
        ("case", "outcome"): ("held",),
        ("expected", "outcome"): ("no_action",),
        ("case", "tier"): ("human_nonmoney", "controller_override"),
        ("expected", "tier"): ("human_nonmoney", "controller_override"),
        ("approval", "tier"): ("human_nonmoney", "controller_override"),
        ("adapter_call", "outcome"): ("conflict",),
        ("audit_event", "type"): ("hold", "veto", "attest", "clear", "handback"),
        ("audit", "chain"): ("hash-chain/2",),
    }
}


class UnsupportedMinor(ValueError):
    """The export declares a schema minor this reader does not know."""


def declared_minor(doc: dict[str, Any]) -> int:
    """The minor an export declares (0 when absent). Raises `UnsupportedMinor` for anything unusable."""
    raw = doc.get("schema_minor", 0)
    if type(raw) is not int or raw < 0:
        raise UnsupportedMinor(f"schema_minor must be a non-negative integer, got {raw!r}")
    if raw > MAX_MINOR:
        raise UnsupportedMinor(f"schema_minor {raw} is newer than this reader (max {MAX_MINOR})")
    return raw


def _rows(doc: dict[str, Any], key: str) -> list[dict[str, Any]]:
    v = doc.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def contexts(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every object of the export tagged with its schema context."""
    out: list[tuple[str, dict[str, Any]]] = [("root", doc)]
    audit = doc.get("audit")
    if isinstance(audit, dict):
        out.append(("audit", audit))
        out += [("audit_event", e) for e in _rows({"e": audit.get("events")}, "e")]
    sc = doc.get("scorecard")
    if isinstance(sc, dict):
        out.append(("scorecard", sc))
    if isinstance(doc.get("run"), dict):
        out.append(("run", doc["run"]))
    for c in _rows(doc, "cases"):
        out.append(("case", c))
        if isinstance(c.get("expected"), dict):
            out.append(("expected", c["expected"]))
    for a in _rows(doc, "approvals"):
        out.append(("approval", a))
        out += [("approver", p) for p in _rows({"p": a.get("approvers")}, "p")]
    out += [("adapter_call", x) for x in _rows(doc, "adapter_calls")]
    return out


def features_used(doc: dict[str, Any]) -> dict[str, int]:
    """Feature name -> the minor that introduced it, for everything in `doc` that is newer than minor 0."""
    found: dict[str, int] = {}
    for ctx, obj in contexts(doc):
        for minor, by_ctx in KEYS_ADDED.items():
            for key in by_ctx.get(ctx, ()):
                if key in obj:
                    found[f"{ctx}.{key}"] = max(found.get(f"{ctx}.{key}", 0), minor)
        for minor, by_key in ENUMS_ADDED.items():
            for (c2, key), values in by_key.items():
                if c2 == ctx and isinstance(obj.get(key), str) and obj[key] in values:
                    found[f"{ctx}.{key}={obj[key]}"] = minor
    return found


def minor_problems(doc: dict[str, Any]) -> list[str]:
    """Problems with the declared minor against the features used. Empty list = consistent."""
    try:
        have = declared_minor(doc)
    except UnsupportedMinor as err:
        return [str(err)]
    return [
        f"{name} was introduced in schema minor {need}; this export declares {have}"
        for name, need in sorted(features_used(doc).items())
        if need > have
    ]


def all_features() -> dict[str, int]:
    """Every feature name that exists in this reader, with its minor (used by the docs and the consistency tests)."""
    out: dict[str, int] = {}
    for minor, by_ctx in KEYS_ADDED.items():
        for ctx, keys in by_ctx.items():
            out.update({f"{ctx}.{k}": minor for k in keys})
    for minor, by_key in ENUMS_ADDED.items():
        for (ctx, key), values in by_key.items():
            out.update({f"{ctx}.{key}={v}": minor for v in values})
    return out


def migrate(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return `(upgraded copy, open problems)`. Only declares the new minor; it cannot invent the new data.

    A minor-0 export has no tenant, no terminal states and a hash-chain/1 audit trail; hash-chain/2 covers the
    tenant, so the chain cannot be upgraded without re-sealing, which would make a forged trail indistinguishable
    from a genuine one. `migrate` therefore leaves minor-0 exports at minor 0 and says so. Re-export from the demo.
    """
    declared_minor(doc)
    if doc.get("schema_minor", 0) == MAX_MINOR:
        return dict(doc), []
    return dict(doc), [
        "minor-0 export stays minor 0: tenant_id, terminal states and hash-chain/2 need a re-export from the demo"
    ]


def _resolve(schema: dict[str, Any], node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return node


_LOWER_BOUNDS = ("minLength", "minItems", "minimum", "minProperties", "exclusiveMinimum")
_UPPER_BOUNDS = ("maxLength", "maxItems", "maximum", "maxProperties", "exclusiveMaximum")
_STRUCTURAL = (
    "oneOf", "anyOf", "allOf", "not", "if", "then", "else", "patternProperties", "propertyNames",
    "prefixItems", "contains", "uniqueItems", "dependentRequired", "dependentSchemas",
    "unevaluatedProperties", "unevaluatedItems", "minContains", "maxContains",
)  # fmt: skip


def additive_diff(
    old: dict[str, Any], new: dict[str, Any], accepted: dict[str, str] | None = None
) -> list[str]:
    """Problems that make `new` a non-additive change of `old`. Walks both documents in parallel, resolving `$ref`.

    Checked: a property removed; a new required key; an enum value removed; a const or type narrowed; a lower bound
    raised or an upper bound lowered or newly added (min/max Length, Items, Properties, minimum, maximum); a `pattern`
    added or changed (a regular expression cannot be proven to accept everything the old one did); `additionalProperties`
    tightened; any added or changed combinator or structural keyword (oneOf/anyOf/allOf/not/if/then/else,
    patternProperties, propertyNames, prefixItems, contains, uniqueItems, dependent*, unevaluated*), because the
    comparison cannot prove those only loosen. Not checked: semantic equivalence of two different `$ref` targets beyond
    these keywords, and anything deeper than 30 levels.

    `accepted` maps "<path>: <keyword>" to the reason a flagged change was reviewed and kept; such a change is not
    reported (the reasons live in `ACCEPTED_TIGHTENINGS`, and a test fails if an entry no longer matches anything).
    """
    problems: list[str] = []
    seen_accepted: set[str] = set()
    accepted = accepted or {}

    def flag(path: str, keyword: str, text: str) -> None:
        key = f"{path}: {keyword}"
        if key in accepted:
            seen_accepted.add(key)
        else:
            problems.append(f"{key} {text}")

    def walk(o: Any, n: Any, path: str, depth: int = 0) -> None:
        o, n = _resolve(old, o), _resolve(new, n)
        if depth > 30 or not isinstance(o, dict) or not isinstance(n, dict):
            return
        if "enum" in o and not set(map(repr, o["enum"])) <= set(
            map(repr, n.get("enum", o["enum"]))
        ):
            problems.append(f"{path}: enum values removed")
        if "const" in o and n.get("const", o["const"]) != o["const"]:
            problems.append(f"{path}: const changed")
        if "type" in o and "type" in n and o["type"] != n["type"]:
            ot, nt = (
                set(o["type"] if isinstance(o["type"], list) else [o["type"]]),
                set(n["type"] if isinstance(n["type"], list) else [n["type"]]),
            )
            if not ot <= nt:
                problems.append(f"{path}: type narrowed {sorted(ot)} -> {sorted(nt)}")
        if set(n.get("required", [])) - set(o.get("required", [])):
            problems.append(
                f"{path}: new required keys {sorted(set(n['required']) - set(o.get('required', [])))}"
            )
        for kw in _LOWER_BOUNDS:
            if kw in n and (kw not in o or n[kw] > o[kw]):
                flag(path, kw, f"raised or added ({o.get(kw)} -> {n[kw]})")
        for kw in _UPPER_BOUNDS:
            if kw in n and (kw not in o or n[kw] < o[kw]):
                flag(path, kw, f"lowered or added ({o.get(kw)} -> {n[kw]})")
        if "pattern" in n and o.get("pattern") != n["pattern"]:
            flag(path, "pattern", "added or changed (cannot be proven to accept every old value)")
        if n.get("additionalProperties") is False and o.get("additionalProperties") is not False:
            flag(path, "additionalProperties", "tightened to false")
        if (
            isinstance(n.get("additionalProperties"), dict)
            and "additionalProperties" not in o
            and o.get("type") == "object"
        ):
            flag(path, "additionalProperties", "a schema was added where any value was allowed")
        for kw in _STRUCTURAL:
            if kw in n and o.get(kw) != n[kw]:
                flag(path, kw, "added or changed (cannot be proven only to loosen)")
        for key, sub in o.get("properties", {}).items():
            if key not in n.get("properties", {}):
                problems.append(f"{path}/{key}: property removed")
            else:
                walk(sub, n["properties"][key], f"{path}/{key}", depth + 1)
        if isinstance(o.get("items"), dict) and isinstance(n.get("items"), dict):
            walk(o["items"], n["items"], f"{path}[]", depth + 1)
        if isinstance(o.get("additionalProperties"), dict) and isinstance(
            n.get("additionalProperties"), dict
        ):
            walk(o["additionalProperties"], n["additionalProperties"], f"{path}/*", depth + 1)

    walk(old, new, "")
    stale = sorted(set(accepted) - seen_accepted)
    problems += [f"{k} is accepted but no longer flagged (stale entry)" for k in stale]
    return problems


# Reviewed changes that `additive_diff` flags and that were kept on purpose. Honest status: the first two are
# LOOSENINGS the diff cannot prove (a pattern that is a superset of the old one); the others marked tightening are real
# TIGHTENINGS of the unreleased minor-0 shape (no exporter outside this branch existed; every example complies).
ACCEPTED_TIGHTENINGS: dict[str, str] = {
    "/cases[]/hard_zero_flags[]: pattern": "loosening: the key pattern (upper case allowed) is a superset of the slug pattern",
    "/scorecard/hard_zero_defs[]/key: pattern": "loosening: the key pattern (upper case allowed) is a superset of the slug pattern",
    "/cases[]/steps[]/source_value: maxLength": "tightening: 2000 characters, the same limit every other text field has; bounds untrusted input",
    "/cases[]/steps[]/model_value: maxLength": "tightening: 2000 characters, the same limit every other text field has; bounds untrusted input",
    "/cases[]/case_id: pattern": "loosening: any slug; the bucket letter and three-digit number were this kit's convention, not a need",
    "/approvals[]/case_id: pattern": "loosening: any slug (same reason as the case id)",
    "/audit/events[]/case_id: pattern": "loosening: any slug or empty (same reason as the case id)",
    "/scorecard/hard_zeros: propertyNames": "tightening: keys must be ASCII ids (unique ignoring case); a look-alike key was a bypass",
}  # fmt: skip

# Features with no producer yet: kept optional and reserved, not promised. They can still change while minor 1 is draft.
RESERVED = (
    "root.ledger",
    "root.load_profile",
    "root.read_log",
    "root.provider_terms",
    "root.segregation",
    "approver.sig_scheme",
)
STATUS = "draft"  # minor 1 stays draft until a real demo exporter passes kitcheck on its own output
