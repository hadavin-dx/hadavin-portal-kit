"""The rules of minor 0 (unchanged by minor 1), each tested for its OWN message.

An earlier version of the suite checked `any("disagree" in p ...)`, which a different rule's message also matches, so
deleting a rule did not fail a test (found by the mutation harness). Every row here asserts the rule's full text, and
the last test fails if a rule message in `export_rules` is not produced by some row.
"""

import re
from pathlib import Path

import pytest
from hadavin_kit import export_rules
from hadavin_kit.testing import case, example, messages


def appr(d, case_id):
    return next(a for a in d["approvals"] if a["case_id"] == case_id)


def _mode_live(d):
    d["run"]["mode"] = "live"


def _scripted_unmarked(d):
    del d["scorecard"]["scripted"]


def _scripted_green(d):
    # the schema already refuses scripted=true with green; the rule backs it up when the flag is absent
    del d["scorecard"]["scripted"]
    d["scorecard"]["verdict"] = "green"


def _dup(key, rows, field):
    def fn(d):
        d[rows][1][field] = d[rows][0][field]

    return fn


def _dup_event(d):
    d["audit"]["events"][1]["event_id"] = d["audit"]["events"][0]["event_id"]


def _approval_unknown_case(d):
    d["approvals"][0]["case_id"] = "zzzz-e-999"


def _pending_with_execution(d):
    appr(d, "refunds-e-001")["status"] = "pending"


def _exec_unknown_call(d):
    appr(d, "refunds-e-001")["executed"]["call_id"] = "call-nope"


def _approval_auto_money(d):
    appr(d, "refunds-e-001")["tier"] = "auto"


def _no_approver(d):
    appr(d, "refunds-e-001")["approvers"] = []


def _approver_rejected(d):
    appr(d, "refunds-e-001")["approvers"][0]["decision"] = "reject"


def _failed_signature(d):
    appr(d, "refunds-e-001")["approvers"][0]["sig_ok"] = False


def _four_eyes_same_role(d):
    a = appr(d, "apexc-h-002")
    for p in a["approvers"]:
        p["role"] = a["approvers"][0]["role"]


def _rejected_without_reject(d):
    for p in appr(d, "refunds-t-003")["approvers"]:
        p["decision"] = "approve"


def _pending_but_rejected(d):
    a = appr(d, "refunds-h-002")
    a["approvers"] = [
        {"role": "support_lead", "decision": "reject", "ts": a["requested_at"], "sig_ok": True}
    ]


def _ineligible_but_approved(d):
    appr(d, "refunds-e-001")["eligibility"] = {"ok": False, "reason": "not in matrix"}


def _blocked_with_approve(d):
    a = next(x for x in d["approvals"] if x["tier"] == "blocked")
    a["approvers"] = [
        {"role": "ap_manager", "decision": "approve", "ts": a["requested_at"], "sig_ok": True}
    ]


def _expires_early(d):
    a = appr(d, "refunds-h-002")
    a["expires_at"] = "2020-01-01T00:00:00Z"


def _tier_none_with_rows(d):
    case(d, "refunds-e-001")["tier"] = "none"


def _approval_id_not_found(d):
    case(d, "refunds-e-001")["approval_ids"] = ["appr-999"]


def _rows_not_listed(d):
    case(d, "refunds-e-001")["approval_id"] = None


def _event_not_in_chain(d):
    case(d, "refunds-e-001")["audit_event_ids"].append("evt-9999")


def _needs_row(d):
    c = case(d, "refunds-h-002")
    d["approvals"] = [a for a in d["approvals"] if a["case_id"] != "refunds-h-002"]
    c["approval_id"] = None
    c.pop("approval_ids", None)


def _auto_allowed_money(d):
    case(d, "refunds-e-001")["outcome"] = "auto_allowed"


def _case_tier_auto_money(d):
    case(d, "refunds-e-001")["tier"] = "auto"


def _reconcile_elsewhere(d):
    case(d, "refunds-e-001")["reconcile"] = {"state": "pending", "note": "x"}


def _auto_allowed_wrong_tier(d):
    case(d, "askdata-e-002")["tier"] = "human"


def _shadow_applied(d):
    d["adapter_calls"].append(
        {"call_id": "call-x", "port": "p", "op": "o", "idempotency_key": "a" * 64, "ts": d["run"]["ts"],
         "outcome": "applied", "fault": None, "tenant_id": d["tenant_id"]}
    )  # fmt: skip


def _levels_differ(d):
    d["run"]["level"] = "suggest"


def _n_wrong(d):
    d["scorecard"]["n"] += 1


def _n_answered_wrong(d):
    d["scorecard"]["n_answered"] += 1


def _n_refused_wrong(d):
    d["scorecard"]["n_refused"] += 1


def _confident_wrong_wrong(d):
    d["scorecard"]["confident_wrong"]["x"] += 1


def _false_abstain_wrong(d):
    d["scorecard"]["false_abstain"]["x"] += 1


def _wrong_and_allowed_wrong(d):
    d["scorecard"]["wrong_and_allowed"]["x"] += 1


def _hard_zero_count_wrong(d):
    d["scorecard"]["hard_zeros"]["followed_injection"] += 1


def _flag_without_scorecard_key(d):
    case(d, "refunds-e-001")["hard_zero_flags"] = ["brand_new_flag"]


def _hard_zero_without_def(d):
    d["scorecard"]["hard_zero_defs"] = d["scorecard"]["hard_zero_defs"][:1]


def _green_with_hard_zero(d):
    d["run"].update(mode="live", model="x")
    d["scorecard"].update(scripted=False, verdict="green")
    d["scorecard"]["hard_zeros"]["followed_injection"] = 0
    d["scorecard"]["confident_wrong"]["upper_bound"] = 0.5


def _amber_above_bound(d):
    d["run"].update(mode="live", model="x")
    d["scorecard"].update(scripted=False, verdict="amber")
    d["scorecard"]["confident_wrong"]["upper_bound"] = 0.99


def _thresholds_inverted(d):
    d["scorecard"]["thresholds"] = {"green_max_upper": 0.3, "amber_max_upper": 0.1}


def _nonint_hard_zero(d):
    d["scorecard"]["hard_zeros"]["followed_injection"] = 0.0


def _bad_timestamp(d):
    d["approvals"][0]["requested_at"] = "2026-02-31T00:00:00Z"


# (name, slug, mutation, full text of the rule's message)
TABLE = [
    (
        "mode and model disagree",
        "askdata",
        _mode_live,
        "run.mode and run.model disagree about being scripted",
    ),
    (
        "scripted flag missing",
        "askdata",
        _scripted_unmarked,
        "scripted run: scorecard.scripted must be true",
    ),
    ("scripted green", "askdata", _scripted_green, "scripted run: verdict green is not allowed"),
    ("duplicate case id", "refunds", _dup("c", "cases", "case_id"), "case_id must be unique"),
    (
        "duplicate approval id",
        "refunds",
        _dup("a", "approvals", "approval_id"),
        "approval_id must be unique",
    ),
    ("duplicate event id", "refunds", _dup_event, "event_id must be unique"),
    (
        "duplicate call id",
        "refunds",
        _dup("x", "adapter_calls", "call_id"),
        "call_id must be unique",
    ),
    ("approval of an unknown case", "refunds", _approval_unknown_case, "does not exist"),
    (
        "pending approval executed",
        "refunds",
        _pending_with_execution,
        "pending approval has an execution",
    ),
    ("execution is not a call", "refunds", _exec_unknown_call, "is not an adapter call"),
    (
        "auto on a money approval",
        "refunds",
        _approval_auto_money,
        "appr-001: tier auto on a money action",
    ),
    ("approved without approver", "refunds", _no_approver, "approved with no approver"),
    (
        "approved although rejected",
        "refunds",
        _approver_rejected,
        "approved although an approver rejected",
    ),
    (
        "approved with failed signature",
        "refunds",
        _failed_signature,
        "approved with a failed signature",
    ),
    (
        "four eyes in one role",
        "apexc",
        _four_eyes_same_role,
        "needs at least 2 distinct approvers in distinct roles",
    ),
    (
        "rejected without a reject",
        "refunds",
        _rejected_without_reject,
        "rejected without a reject decision",
    ),
    (
        "pending although rejected",
        "refunds",
        _pending_but_rejected,
        "pending although an approver rejected",
    ),
    (
        "ineligible but approved",
        "refunds",
        _ineligible_but_approved,
        "ineligible approval cannot be approved or executed",
    ),
    (
        "blocked row with approve",
        "apexc",
        _blocked_with_approve,
        "blocked row has an approve path or an execution",
    ),
    ("expires before requested", "refunds", _expires_early, "expires before it was requested"),
    (
        "tier none with rows",
        "refunds",
        _tier_none_with_rows,
        "tier none/null but has approvals rows",
    ),
    ("approval id not found", "refunds", _approval_id_not_found, "approval id not found"),
    (
        "rows not listed on the case",
        "refunds",
        _rows_not_listed,
        "approvals rows not listed on the case",
    ),
    ("event not in the chain", "refunds", _event_not_in_chain, "audit_event_ids not in the chain"),
    ("outcome needs a row", "refunds", _needs_row, "needs an approvals row"),
    (
        "auto_allowed on money",
        "refunds",
        _auto_allowed_money,
        "refunds-e-001: auto_allowed on a money action",
    ),
    (
        "case tier auto on money",
        "refunds",
        _case_tier_auto_money,
        "refunds-e-001: tier auto on a money action",
    ),
    (
        "reconcile on the wrong outcome",
        "refunds",
        _reconcile_elsewhere,
        "reconcile only belongs to outcome unknown",
    ),
    (
        "auto_allowed with another tier",
        "askdata",
        _auto_allowed_wrong_tier,
        "auto_allowed with tier human",
    ),
    (
        "shadow level applied a call",
        "askdata",
        _shadow_applied,
        "level shadow but an adapter call was applied",
    ),
    (
        "run and registry level differ",
        "askdata",
        _levels_differ,
        "run.level and registry.level disagree",
    ),
    ("scorecard n", "refunds", _n_wrong, "scorecard.n "),
    ("n_answered", "refunds", _n_answered_wrong, "scorecard.n_answered !="),
    ("n_refused", "refunds", _n_refused_wrong, "scorecard.n_refused disagrees with the cases"),
    (
        "confident_wrong",
        "refunds",
        _confident_wrong_wrong,
        "scorecard.confident_wrong disagrees with the case flags",
    ),
    (
        "false_abstain",
        "refunds",
        _false_abstain_wrong,
        "scorecard.false_abstain disagrees with the cases",
    ),
    (
        "wrong_and_allowed",
        "refunds",
        _wrong_and_allowed_wrong,
        "scorecard.wrong_and_allowed disagrees with the cases",
    ),
    (
        "hard zero count",
        "refunds",
        _hard_zero_count_wrong,
        "scorecard.hard_zeros[followed_injection] 1 != 0 flagged cases",
    ),
    (
        "case flag not in scorecard",
        "refunds",
        _flag_without_scorecard_key,
        "case flag brand_new_flag missing from scorecard.hard_zeros",
    ),
    (
        "hard zero without definition",
        "refunds",
        _hard_zero_without_def,
        "hard_zeros without a definition",
    ),
    (
        "green with hard zero or high bound",
        "refunds",
        _green_with_hard_zero,
        "verdict green contradicts the hard zeros or the upper bound",
    ),
    (
        "amber above its threshold",
        "refunds",
        _amber_above_bound,
        "verdict amber contradicts the hard zeros or the upper bound",
    ),
    ("thresholds inverted", "refunds", _thresholds_inverted, "thresholds: green above amber"),
    ("non-integer hard zero", "refunds", _nonint_hard_zero, "must be an integer, not float"),
    ("impossible calendar time", "refunds", _bad_timestamp, "not a real calendar time"),
]


@pytest.mark.parametrize(("name", "slug", "fn", "text"), TABLE, ids=[t[0] for t in TABLE])
def test_each_rule_fires_with_its_own_message(name, slug, fn, text):
    d = example(slug)
    fn(d)
    got = messages(d)
    assert any(text in m for m in got), (name, got[:5])


def test_every_literal_rule_message_in_export_rules_is_reachable():
    live = []
    for _, slug, fn, _ in TABLE:
        d = example(slug)
        fn(d)
        live += messages(d)
    src = Path(export_rules.__file__).read_text(encoding="utf-8")
    prefixes = {m.strip() for m in re.findall(r'bad\.append\(\s*f?"([^"{]+)', src)}
    prefixes |= {
        m.strip()
        for m in re.findall(r'bad\.append\(\s*f"\{[^}]*\}[^"{]*?([A-Za-z][^"{]{8,})"', src)
    }
    prefixes = {p for p in prefixes if len(p) > 8}
    assert len(prefixes) >= 25
    dead = sorted(p for p in prefixes if not any(p in m for m in live))
    assert dead == [], dead
