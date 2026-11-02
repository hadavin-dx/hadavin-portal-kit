"""What the console may and may not say about an export. Python reference; `console/app.js` computes the same ids and
writes them to `data-honest` on the page, and a browser test compares the two for every example.

Rules: a scripted run is never green (the verdict shown is capped at amber and the heading says plumbing check); an
export that is not marked `honesty.kind: run` is a fixture and says so on every page; an assumed load profile, a
missing provider no-training record and a canary test that did not run are shown as such, never as a pass.
"""

from __future__ import annotations

from typing import Any

from hadavin_kit import chain

SCRIPTED_TEXT = "scripted; gate vs gold agreement; not model accuracy"


def is_scripted(doc: dict[str, Any]) -> bool:
    return (
        doc["run"]["mode"] == "scripted"
        or doc["run"]["model"] == "scripted"
        or doc["scorecard"].get("scripted") is True
    )


def is_fixture(doc: dict[str, Any]) -> bool:
    return doc["honesty"].get("kind") != "run"


def shown_verdict(doc: dict[str, Any]) -> str:
    v = doc["scorecard"]["verdict"]
    return "amber" if is_scripted(doc) and v == "green" else v


def ledger_breached(doc: dict[str, Any]) -> bool:
    led = doc.get("ledger")
    return led is not None and any(
        led[k] for k in ("non_terminal", "missing", "duplicated_effects", "reconciliation_gap")
    )


def status_ids(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if is_fixture(doc):
        out.append("fixture")
    if is_scripted(doc):
        out.append("scripted")
        if doc["scorecard"]["verdict"] == "green":
            out.append("capped_verdict")
    got, claim = chain.verify(doc["audit"]["events"], doc["audit"]["chain"]), doc["audit"]["verify"]
    if not got["ok"] or any(got[k] != claim[k] for k in ("events", "matched", "ok")):
        out.append("chain_bad")  # the chain fails, or the export's own claim about it is false
    lp = doc.get("load_profile")
    if lp is not None:
        out.append("load_measured" if lp["label"] == "measured" else "load_assumed")
    pt = doc.get("provider_terms")
    if pt is not None and pt["no_training_setting"] is None:
        out.append("terms_unrecorded")
    seg = doc.get("segregation")
    if seg is not None:
        if not seg["canary"]["ran"]:
            out.append("canary_not_run")
        elif seg["canary"]["foreign_hits"]:
            out.append("canary_breach")
    if ledger_breached(doc):
        out.append("ledger_breach")
    return out
