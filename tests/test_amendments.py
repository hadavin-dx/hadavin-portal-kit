"""Contract amendments 2026-11-01: each one has a positive example and a rejected negative one."""

import copy
import json
from pathlib import Path

import pytest
from hadavin_kit import export_rules
from jsonschema import Draft202012Validator

KIT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8"))
V = Draft202012Validator(SCHEMA)


def doc(slug):
    return json.loads((KIT / "demo_only" / "examples" / f"{slug}.json").read_text(encoding="utf-8"))


def case(d, cid):
    return next(c for c in d["cases"] if c["case_id"] == cid)


def ok(d):
    return not list(V.iter_errors(d)) and not export_rules.check(d)


def bad(d):
    return bool(list(V.iter_errors(d))) or bool(export_rules.check(d))


def mut(slug, fn):
    d = copy.deepcopy(doc(slug))
    fn(d)
    return d


# (1) no_action
def test_no_action_outcome():
    d = doc("apexc")
    assert case(d, "apexc-e-003")["outcome"] == "no_action" and ok(d)
    assert bad(mut("apexc", lambda x: case(x, "apexc-e-003").update(outcome="nothing")))


# (2) case tier, four_eyes_over_max, tier none means no approvals row
def test_case_tier_and_over_max():
    d = doc("apexc")
    assert case(d, "apexc-h-002")["tier"] == "four_eyes_over_max"
    assert {a["tier"] for a in d["approvals"]} >= {"four_eyes_over_max", "blocked"}
    assert ok(d)
    assert bad(mut("apexc", lambda x: case(x, "apexc-h-002").update(tier="sometimes")))
    assert bad(
        mut("apexc", lambda x: case(x, "apexc-h-002").update(tier=None))
    )  # null tier but has an approvals row
    assert bad(mut("apexc", lambda x: case(x, "apexc-h-002").update(tier="none")))


def test_auto_is_never_allowed_for_money():
    for slug in ("refunds", "apexc", "intake", "dealdesk", "askdata"):
        d = doc(slug)
        money = export_rules.money_tools(d)
        for a in d["approvals"]:
            assert not (a["tier"] == "auto" and export_rules.is_money_approval(d, a)), (
                slug,
                a["approval_id"],
            )
        for c in d["cases"]:
            touches_money = bool(money & {s["name"] for s in c["steps"]})
            assert not (c["outcome"] == "auto_allowed" and touches_money), (slug, c["case_id"])
    assert bad(mut("refunds", lambda x: x["approvals"][0].update(tier="auto")))
    assert bad(mut("refunds", lambda x: case(x, "refunds-e-001").update(outcome="auto_allowed")))
    assert bad(mut("refunds", lambda x: case(x, "refunds-e-001").update(tier="auto")))
    d = doc("askdata")  # auto is fine for a read-only, non-money query
    assert case(d, "askdata-e-003")["tier"] == "auto" and ok(d)


# (3) approval_ids, ordered approvers, nullable executed, rule_ids
def test_multiple_approvals_and_ordered_approvers():
    d = doc("apexc")
    c = case(d, "apexc-t-002")
    assert len(c["approval_ids"]) == 3 and c["approval_id"] == c["approval_ids"][0] and ok(d)
    two = next(a for a in d["approvals"] if a["case_id"] == "apexc-h-002")
    assert [p["role"] for p in two["approvers"]] == ["ap_manager", "finance_controller"]
    assert any(a["executed"] is None and a["status"] == "pending" for a in d["approvals"])
    assert any("rule_ids" in a for a in d["approvals"])
    assert bad(mut("apexc", lambda x: case(x, "apexc-t-002").update(approval_ids=["appr-999"])))
    assert bad(mut("apexc", lambda x: x["approvals"][0].update(rule_ids=["nope"])))
    assert bad(
        mut("apexc", lambda x: case(x, "apexc-t-002").pop("approval_ids"))
    )  # rows not listed on the case
    pending = next(a for a in doc("apexc")["approvals"] if a["status"] == "pending")

    def execute(x):
        next(a for a in x["approvals"] if a["approval_id"] == pending["approval_id"])["executed"] = {
            "port": "p", "call_id": "call-1", "result": "ok", "ts": "2026-11-01T09:00:00Z"
        }  # fmt: skip

    assert bad(mut("apexc", execute))  # a pending approval cannot have executed


# (4) exceptions, reconcile, expected extras
def test_exceptions_reconcile_and_expected_extras():
    a = doc("apexc")
    c = case(a, "apexc-t-002")
    assert c["exceptions"][0] == {"code": "bank_change", "rule_id": "R-APEXC-004"}
    assert set(c["expected"]) >= {"tier", "exceptions", "actions"}
    r = doc("refunds")
    assert case(r, "refunds-h-003")["reconcile"]["state"] == "pending" and ok(r)
    assert bad(mut("refunds", lambda x: case(x, "refunds-h-003")["reconcile"].update(state="done")))
    assert bad(
        mut(
            "refunds",
            lambda x: case(x, "refunds-e-001").update(reconcile={"state": "pending", "note": "n"}),
        )
    )
    assert bad(
        mut("apexc", lambda x: case(x, "apexc-t-002")["exceptions"][0].update(rule_id="bad"))
    )


# (5) steps: reconcile and attest kinds, optional actor, documented source/model values
def test_step_kinds_and_actor():
    kinds = {
        s["kind"] for slug in ("refunds", "apexc") for c in doc(slug)["cases"] for s in c["steps"]
    }
    assert {"reconcile", "attest"} <= kinds
    assert any(
        s.get("actor") == "human:treasury" for c in doc("apexc")["cases"] for s in c["steps"]
    )
    assert bad(mut("apexc", lambda x: case(x, "apexc-t-002")["steps"][2].update(kind="magic")))
    assert bad(mut("apexc", lambda x: case(x, "apexc-t-002")["steps"][2].update(actor="Mallory")))
    assert (
        "system of record" in SCHEMA["$defs"]["step"]["properties"]["source_value"]["description"]
    )


# (6) adapter reads; ts and port are required
def test_adapter_reads_and_required_fields():
    d = doc("apexc")
    assert {c["outcome"] for c in d["adapter_calls"]} >= {"read_ok", "read_fault"} and ok(d)
    for key in ("ts", "port"):
        assert bad(mut("apexc", lambda x, k=key: x["adapter_calls"][0].pop(k)))
    assert bad(mut("apexc", lambda x: x["adapter_calls"][0].update(outcome="read_maybe")))


# (7) scorecard n_refused and scripted
def test_scripted_scorecard_never_green():
    for slug in ("refunds", "apexc", "intake", "dealdesk", "askdata"):
        sc = doc(slug)["scorecard"]
        assert sc["scripted"] is True and sc["verdict"] != "green" and "n_refused" in sc
    assert bad(mut("askdata", lambda x: x["scorecard"].update(verdict="green")))
    # a scripted run can not claim scripted=False to reach green (run.mode and run.model still say scripted)
    assert bad(mut("askdata", lambda x: x["scorecard"].update(verdict="green", scripted=False)))
    assert bad(mut("askdata", lambda x: x["scorecard"].update(scripted="yes")))
    assert bad(mut("askdata", lambda x: x["scorecard"].update(n_refused=-1)))


def test_n_answered_counts_expected_act_no_action_or_refuse():
    for slug in ("refunds", "apexc", "intake", "dealdesk", "askdata"):
        d = doc(slug)
        assert d["scorecard"]["n_answered"] == sum(
            c["expected"]["outcome"] in ("act", "no_action", "refuse") for c in d["cases"]
        )
        assert d["scorecard"]["n_refused"] == sum(
            c["expected"]["outcome"] == "refuse" for c in d["cases"]
        )


# (8) eligibility
def test_eligibility_is_visible():
    d = doc("dealdesk")
    inel = [a for a in d["approvals"] if not a["eligibility"]["ok"]]
    assert inel and "not sufficient" in inel[0]["eligibility"]["reason"] and ok(d)
    assert bad(mut("dealdesk", lambda x: x["approvals"][0].update(eligibility={"ok": True})))
    assert bad(
        mut(
            "dealdesk", lambda x: x["approvals"][0].update(eligibility={"ok": "yes", "reason": "r"})
        )
    )


# old exports (without any new key) still validate: all new keys are optional
@pytest.mark.parametrize("slug", ["refunds", "apexc", "intake", "dealdesk", "askdata"])
def test_exports_without_new_keys_still_validate(slug):
    d = doc(slug)
    for c in d["cases"]:
        for k in ("tier", "approval_ids", "exceptions", "reconcile"):
            c.pop(k, None)
        for k in ("tier", "exceptions", "actions"):
            c["expected"].pop(k, None)
        for s in c["steps"]:
            s.pop("actor", None)
    for a in d["approvals"]:
        a.pop("eligibility", None)
        a.pop("rule_ids", None)
    d["scorecard"].pop("scripted")
    d["scorecard"].pop("n_refused")
    errs = [e.message for e in V.iter_errors(d)]
    # the only legitimate failures: attest/reconcile kinds, read outcomes and no_action are new enum values,
    # and rows no longer listed on a multi-approval case (checked by export_rules, not the schema)
    assert not errs, errs
