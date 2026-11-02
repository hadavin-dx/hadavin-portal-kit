"""Generates the tables and counts in CONTRACT.md from the schema asks ledger and the code, so no number is typed by hand.

`python -m hadavin_kit.docgen` rewrites the block between the markers in `CONTRACT.md`; `tests/test_docs_consistency.py`
fails when the committed block differs from what this module generates.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from hadavin_kit import problems, testing, versioning

KIT = testing.KIT
BEGIN, END = "<!-- generated:begin -->", "<!-- generated:end -->"
STATUS_LABEL = {
    "added": "added in minor 1",
    "already_in_1.0": "already in 1.0",
    "kit_fix": "fixed in the kit, no field",
    "demo_side": "demo side, no field",
    "core_issue": "core issue, no field",
}


def asks() -> list[dict]:
    return json.loads((KIT / "schema" / "asks.v1.json").read_text(encoding="utf-8"))["rows"]


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def generate() -> str:
    rows = asks()
    by_status = Counter(r["status"] for r in rows)
    feats = versioning.all_features()
    out = [
        BEGIN,
        "<!-- Do not edit by hand: python -m hadavin_kit.docgen -->",
        "",
        "## Counts (generated)",
        "",
        f"- Asks collected: {len(rows)} ("
        + ", ".join(f"{by_status[k]} {STATUS_LABEL[k]}" for k in STATUS_LABEL)
        + ").",
        f"- Features added in minor 1: {sum(1 for m in feats.values() if m == 1)}; current maximum minor: {versioning.MAX_MINOR}.",
        f"- Minor 1 status: {versioning.STATUS}. Reserved (no real producer yet): "
        + ", ".join(f"`{r}`" for r in versioning.RESERVED)
        + ".",
        f"- Reviewed schema changes kept despite `additive_diff` flags: {len(versioning.ACCEPTED_TIGHTENINGS)}.",
        f"- Typed problem codes: {len(problems.CODES)} ({', '.join(problems.CODES)}).",
        "- Example exports (all minor " + str(versioning.MAX_MINOR) + "):",
    ]
    for slug in testing.SLUGS:
        d = testing.example(slug)
        out.append(
            f"  - {slug}: {len(d['cases'])} cases, {len(d['approvals'])} approvals, {len(d['audit']['events'])} audit events, "
            f"verdict {d['scorecard']['verdict']}, honesty.kind {d['honesty']['kind']}."
        )
    out += [
        "",
        "## All schema asks (generated)",
        "",
        "| Id | Demo | Ask | Status | Resolution |",
        "|---|---|---|---|---|",
    ]
    out += [
        f"| {r['id']} | {r['demo']} | {_cell(r['ask'])} | {STATUS_LABEL[r['status']]} | {_cell(r['resolution'])} |"
        for r in rows
    ]
    out += ["", "## Conflicts and how they were resolved (generated)", ""]
    out += [
        f"- **{r['id']}** ({r['demo']}): {_cell(r['conflict'])} Resolution: {_cell(r['resolution'])}"
        for r in rows
        if r["conflict"]
    ]
    out += ["", "## Features by minor (generated)", ""]
    for minor in sorted(set(feats.values())):
        out.append(
            f"- Minor {minor}: "
            + ", ".join(f"`{k}`" for k, m in sorted(feats.items()) if m == minor)
        )
    out += ["", END]
    return "\n".join(out)


def splice(text: str, block: str) -> str:
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[:i] + block + text[j:]


FACTS = KIT / "approvals" / "schema-facts.json"


def schema_facts(schema: dict) -> dict[str, dict]:
    """Every required list, enum, pattern and closed-object flag of the schema, keyed by JSON pointer.

    The committed copy (`approvals/schema-facts.json`) is an approval file: a test compares the live schema with it, so
    a schema edit shows up as a diff in review and a silently loosened schema cannot pass (the schema cannot be the only
    judge of itself).
    """
    out: dict[str, dict] = {}

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            fact = {}
            if isinstance(node.get("required"), list):
                fact["required"] = sorted(node["required"])
            if isinstance(node.get("enum"), list):
                fact["enum"] = node["enum"]
            if "pattern" in node:
                fact["pattern"] = node["pattern"]
            for kw in (
                "maxLength",
                "minLength",
                "minimum",
                "maximum",
                "maxItems",
                "minItems",
                "maxProperties",
            ):
                if kw in node and not isinstance(node[kw], dict):
                    fact[kw] = node[kw]
            if node.get("additionalProperties") is False:
                fact["closed"] = True
            if fact:
                out[path or "/"] = fact
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(schema, "")
    return out


def main() -> int:
    if "--update-schema-facts" in sys.argv:
        facts = schema_facts(
            json.loads((KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8"))
        )
        FACTS.write_text(
            json.dumps(facts, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(FACTS)
        return 0
    p = KIT / "CONTRACT.md"
    p.write_text(splice(p.read_text(encoding="utf-8"), generate()), encoding="utf-8", newline="\n")
    print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
