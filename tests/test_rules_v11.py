"""Schema minor 1: every new rule has a baseline that passes and a mutation that must be rejected with the rule's text.

The baselines are the five minor-1 example exports. A rule that no mutation can trigger is dead code, so the last test
checks that each rule message in `rules_v11` is hit by some row of the table (the mutation harness relies on this).
"""

import copy
import re
from pathlib import Path

import pytest
from hadavin_kit import chain, evalcore, export_rules, rules_v11, validate
from hadavin_kit.testing import SLUGS, case, example, messages


def appr(d, case_id):
    return next(a for a in d["approvals"] if a["case_id"] == case_id)


def first(d, key):
    return next(x for x in d[key])


def _tenant_missing(d):
    del d["cases"][0]["tenant_id"]


def _tenant_wrong_approval(d):
    d["approvals"][0]["tenant_id"] = "someone-else"


def _root_tenant_gone(d):
    del d["tenant_id"]


def _event_other_tenant(d):
    d["audit"]["events"][2]["tenant_id"] = "other-tenant"


def _chain_one(d):
    d["audit"]["chain"] = "hash-chain/1"


def _minor_zero(d):
    d["schema_minor"] = 0


def _kind_gone(d):
    del d["honesty"]["kind"]


def _dup_receipt(d):
    d["cases"][1]["receipt_id"] = d["cases"][0]["receipt_id"]


def _dead_letter_wrong_outcome(d):
    case(d, "intake-t-002")["outcome"] = "blocked"


def _abstained_wrong_outcome(d):
    case(d, "intake-e-001" if False else d["cases"][0]["case_id"])["terminal_state"] = {
        "state": "abstained",
        "reason": "x",
    }


def _ledger_sum(d):
    d["ledger"]["received"] += 1


def _ledger_vs_cases(d):
    d["ledger"]["by_state"]["processed"] -= 1
    d["ledger"]["by_state"]["dead_lettered"] += 1


def _ledger_load_run_no_report(d):
    d["ledger"]["scope"] = "load_run"


def _case_without_terminal_state(d):
    del d["cases"][0]["terminal_state"]


def _call_receipt_unknown(d):
    d["adapter_calls"][0]["receipt_id"] = "rcpt-nope"


def _applied_twice(d):
    x = next(c for c in d["adapter_calls"] if c["outcome"] == "applied")
    y = copy.deepcopy(x)
    y["call_id"] = "call-dup-001"
    d["adapter_calls"].append(y)


def _false_hold_wrong(d):
    d["scorecard"]["false_hold"]["x"] = 5


def _held_no_false_hold(d):
    del d["scorecard"]["false_hold"]


def _exposure_unknown_key(d):
    d["scorecard"]["hard_zero_exposure"]["nonexistent_key"] = {"exposed": 0, "of": 1}


def _exposure_impossible(d):
    k = next(iter(d["scorecard"]["hard_zero_exposure"]))
    d["scorecard"]["hard_zero_exposure"][k] = {"exposed": 9, "of": 3}


def _exposure_below_breaches(d):
    # a breach is an exposure: more breaches than exposures is impossible
    d["scorecard"]["hard_zero_exposure"]["quote_without_approvals"]["exposed"] = 0


def _excluded_among_approvers(d):
    a = d["approvals"][0]
    a["approvers"][0]["person_ref"] = "usr-1"
    a["excluded_user_ids"] = ["usr-1"]


def _role_not_in_roles(d):
    d["approvals"][0]["approvers"][0]["roles_at_decision"] = ["nobody"]


def _unsigned_but_ok(d):
    d["approvals"][0]["approvers"][0]["sig_scheme"] = "none"
    d["approvals"][0]["approvers"][0]["sig_ok"] = True


def _nonmoney_on_money(d):
    appr(d, "refunds-e-001")["tier"] = "human_nonmoney"


def _amount_dup(d):
    d["amounts"].append(copy.deepcopy(d["amounts"][0]))


def _measured_no_report(d):
    d["load_profile"].update(label="measured", report_sha256=None)


def _measured_on_fixture(d):
    d["load_profile"].update(label="measured", report_sha256="a" * 64)


def _measured_no_source(d):
    d["honesty"]["kind"] = "run"
    d["load_profile"].update(label="measured", report_sha256="a" * 64, source="   ")


def _read_log_totals(d):
    d["read_log"]["rows_total"] += 1


def _terms_half(d):
    d["provider_terms"]["no_training_setting"] = "excluded"


def _terms_future(d):
    d["provider_terms"].update(no_training_setting="excluded", checked_on="2099-01-01")


def _terms_bad_date(d):
    d["provider_terms"].update(no_training_setting="excluded", checked_on="2026-02-31")


def _namespace_foreign(d):
    d["segregation"]["namespace"] = "other-namespace"


def _canary_hits_without_run(d):
    d["segregation"]["canary"] = {"ran": False, "foreign_hits": 2}


def _hz_case_collision(d):
    sc = d["scorecard"]
    k = next(iter(sc["hard_zeros"]))
    sc["hard_zeros"][k.upper()] = 0


def _event_of(d, case_id, actor):
    return next(
        e
        for e in d["audit"]["events"]
        if e["case_id"] == case_id and e["actor"] == actor and e["type"] == "approval"
    )


def _controller_roles_unstated(d):
    e = _event_of(d, "apexc-h-002", "human:finance_controller")
    e["summary"] = e["summary"].split(" [roles=")[
        0
    ]  # the two-role approver's event no longer states his roles


def _event_states_foreign_roles(d):
    e = _event_of(d, "apexc-h-002", "human:ap_manager")
    e["summary"] = e["summary"].split(" [roles=")[0] + " [roles=ceo]"


def _swapped_between_approvers(d):
    a = _event_of(d, "apexc-h-002", "human:ap_manager")
    b = _event_of(d, "apexc-h-002", "human:finance_controller")
    a["summary"], b["summary"] = (
        a["summary"].split(" [roles=")[0] + " [roles=finance_controller,treasury]",
        b["summary"].split(" [roles=")[0] + " [roles=ap_manager]",
    )


def _roles_not_ids(d):
    d["approvals"][0]["approvers"][0]["roles_at_decision"] = ["Bad Role"]


def _exposure_of_not_n(d):
    d["scorecard"]["hard_zero_exposure"] = {"followed_injection": {"exposed": 2, "of": 3}}


def _exposed_without_at_risk(d):
    d["scorecard"]["hard_zero_exposure"]["followed_injection"].pop("of_cases")


def _at_risk_without_exposed(d):
    d["scorecard"]["hard_zero_exposure"]["followed_injection"].pop("exposed_cases")


def _at_risk_unknown_case(d):
    d["scorecard"]["hard_zero_exposure"]["followed_injection"]["of_cases"][0] = "no-such-case"


def _exposed_not_among_at_risk(d):
    d["scorecard"]["hard_zero_exposure"]["followed_injection"]["exposed_cases"][0] = "no-such-case"


def _breach_not_exposed(d):
    d["scorecard"]["hard_zero_exposure"]["quote_without_approvals"] = {
        "exposed": 1, "of": len(d["cases"]), "of_cases": [c["case_id"] for c in d["cases"]],
        "exposed_cases": ["dealdesk-t-001"],
    }  # fmt: skip


def _steered_unknown_key(d):
    d["scorecard"]["steered"]["exposure_key"] = "nothing"


def _steered_probes_nothing(d):
    d["scorecard"]["hard_zero_exposure"]["followed_injection"].update(exposed=0, exposed_cases=[])


def _scripted_without_recorded_hash(d):
    del d["run"]["recorded_turns_sha256"]


def _live_with_recorded_hash(d):
    d["run"].update(mode="live", model="gpt-x")


# (name, slug, mutation, text that must appear in some problem)
TABLE = [
    ("tenant missing on a case", "refunds", _tenant_missing, "tenant_id is missing"),
    ("tenant of an approval differs", "refunds", _tenant_wrong_approval, "is not the export's"),
    (
        "root tenant removed",
        "refunds",
        _root_tenant_gone,
        "tenant_id is required from schema minor 1",
    ),
    ("audit event of another tenant", "refunds", _event_other_tenant, "is not the export's"),
    ("minor 1 on hash-chain/1", "refunds", _chain_one, "requires audit.chain hash-chain/2"),
    ("hash-chain/2 at minor 0", "refunds", _minor_zero, "hash-chain/2 needs schema minor 1"),
    ("honesty.kind removed", "refunds", _kind_gone, "honesty.kind is required"),
    ("duplicate receipt id", "refunds", _dup_receipt, "receipt_id must be unique"),
    (
        "dead_lettered but not failed",
        "intake",
        _dead_letter_wrong_outcome,
        "dead_lettered needs outcome failed",
    ),
    (
        "abstained terminal on an acted case",
        "refunds",
        _abstained_wrong_outcome,
        "terminal state abstained needs outcome",
    ),
    ("ledger arithmetic", "refunds", _ledger_sum, "ledger.received !="),
    ("ledger against the cases", "refunds", _ledger_vs_cases, "ledger disagrees with the cases"),
    (
        "ledger case without terminal state",
        "refunds",
        _case_without_terminal_state,
        "every case needs terminal_state",
    ),
    (
        "load_run ledger without report",
        "refunds",
        _ledger_load_run_no_report,
        "needs report_sha256",
    ),
    ("call receipt unknown", "refunds", _call_receipt_unknown, "is not on any case"),
    ("one key applied twice", "refunds", _applied_twice, "was applied 2 times"),
    ("false_hold wrong", "apexc", _false_hold_wrong, "false_hold disagrees"),
    ("held case without false_hold", "apexc", _held_no_false_hold, "false_hold is missing"),
    ("exposure of unknown key", "intake", _exposure_unknown_key, "has no hard zero"),
    ("exposure impossible", "intake", _exposure_impossible, "is impossible"),
    (
        "breaches exceed the exposure",
        "dealdesk",
        _exposure_below_breaches,
        "hard zero count exceeds the cases exposed",
    ),
    (
        "excluded user among approvers",
        "dealdesk",
        _excluded_among_approvers,
        "appear among the approvers",
    ),
    (
        "role not in roles_at_decision",
        "dealdesk",
        _role_not_in_roles,
        "is not among roles_at_decision",
    ),
    (
        "two-role approver's event drops his roles",
        "apexc",
        _controller_roles_unstated,
        "is not stated by the hashed approval event",
    ),
    (
        "event states roles nobody declares",
        "apexc",
        _event_states_foreign_roles,
        "that no approval declares",
    ),
    (
        "roles swapped between two approvers",
        "apexc",
        _swapped_between_approvers,
        "is not stated by the hashed approval event",
    ),
    ("roles are not ids", "dealdesk", _roles_not_ids, "values must be ids"),
    ("exposure of is not n", "refunds", _exposure_of_not_n, "list the cases at risk in of_cases"),
    (
        "exposed_cases without of_cases",
        "refunds",
        _exposed_without_at_risk,
        "exposed_cases needs of_cases",
    ),
    (
        "of_cases without exposed_cases",
        "refunds",
        _at_risk_without_exposed,
        "of_cases needs exposed_cases",
    ),
    ("at-risk case does not exist", "refunds", _at_risk_unknown_case, "distinct existing cases"),
    (
        "exposed case not at risk",
        "refunds",
        _exposed_not_among_at_risk,
        "distinct cases among of_cases",
    ),
    (
        "breach not among exposed",
        "dealdesk",
        _breach_not_exposed,
        "every breached case must be among exposed_cases",
    ),
    (
        "steered names an unknown exposure",
        "refunds",
        _steered_unknown_key,
        "names a hard zero with no exposure entry",
    ),
    ("steered probes nothing", "refunds", _steered_probes_nothing, "shows no exposed case"),
    (
        "scripted run without the recorded-turns hash",
        "refunds",
        _scripted_without_recorded_hash,
        "needs run.recorded_turns_sha256",
    ),
    ("live run with a recorded-turns hash", "refunds", _live_with_recorded_hash, "on a live run"),
    ("unsigned decision marked ok", "dealdesk", _unsigned_but_ok, "sig_scheme none"),
    ("human_nonmoney on money", "refunds", _nonmoney_on_money, "human_nonmoney on a money action"),
    ("amounts duplicate row", "refunds", _amount_dup, "one row per (kind, currency)"),
    ("measured without report", "refunds", _measured_no_report, "measured needs report_sha256"),
    ("measured on a fixture", "refunds", _measured_on_fixture, "not allowed on a fixture"),
    ("measured without source", "refunds", _measured_no_source, "needs a source"),
    ("read log totals", "apexc", _read_log_totals, "read_log totals disagree"),
    ("provider terms half set", "refunds", _terms_half, "both null or both set"),
    ("provider terms date in the future", "refunds", _terms_future, "after the run"),
    ("provider terms impossible date", "refunds", _terms_bad_date, "not a real calendar date"),
    (
        "namespace of another tenant",
        "refunds",
        _namespace_foreign,
        "must be the tenant id or start with the tenant id and a dot",
    ),
    (
        "canary hits without a run",
        "refunds",
        _canary_hits_without_run,
        "foreign hits without a run",
    ),
    (
        "hard-zero keys differing in case",
        "refunds",
        _hz_case_collision,
        "same key once case is ignored",
    ),
]


@pytest.mark.parametrize("slug", SLUGS)
def test_baselines_are_valid(slug):
    assert messages(example(slug)) == []


@pytest.mark.parametrize(("name", "slug", "fn", "text"), TABLE, ids=[t[0] for t in TABLE])
def test_mutation_is_rejected_with_its_rule(name, slug, fn, text):
    d = example(slug)
    fn(d)
    got = messages(d)
    assert any(text in m for m in got), (name, got[:4])


def test_hard_zero_case_collision_is_typed_confusable():
    d = example("refunds")
    _hz_case_collision(d)
    assert "CONFUSABLE_ID" in {p.code for p in validate.validate(d)}


def test_every_rule_message_is_reachable_from_the_table():
    """Each literal rule message in rules_v11 appears in the output of at least one table mutation (no dead rules)."""
    live = []
    for _, slug, fn, _ in TABLE:
        d = example(slug)
        fn(d)
        live += messages(d)
    src = Path(rules_v11.__file__).read_text(encoding="utf-8")
    prefixes = {m.strip() for m in re.findall(r'bad\.append\(\s*f?"([^"{]+)', src)}
    assert len(prefixes) >= 20
    dead = sorted(p for p in prefixes if not any(p in m for m in live))
    assert dead == [], dead


# ----------------------------------------------------------------------------- semantics of the new outcomes
def test_false_hold_is_reported_beside_false_abstain_never_blended():
    d = example("apexc")
    held = case(d, "apexc-a-002")
    assert held["outcome"] == "held" and held["expected"]["outcome"] == "abstain"
    sc = d["scorecard"]
    assert sc["false_hold"] == {
        "x": 0,
        "n": sum(c["expected"]["outcome"] in ("act", "no_action") for c in d["cases"]),
    }
    # a held case that should have been acted on is a false hold and NOT a false abstain
    d2 = copy.deepcopy(d)
    c = case(d2, "apexc-e-001")
    for ap in [a for a in d2["approvals"] if a["case_id"] == "apexc-e-001"]:
        d2["approvals"].remove(ap)
    c.update(outcome="held", approval_id=None, tier="none", correct=False)
    c.pop("approval_ids", None)
    d2["adapter_calls"] = [
        x for x in d2["adapter_calls"] if x.get("receipt_id") != "rcpt-apexc-e-001"
    ]
    d2["scorecard"]["false_hold"]["x"] = 1
    got = messages(d2)
    assert not any("false_hold disagrees" in m for m in got)
    assert any("false_abstain" in m for m in got) is False


def test_no_action_expected_counts_as_answered_and_false_abstain_domain():
    d = example("intake")
    ex = case(d, "intake-e-020")
    assert ex["expected"]["outcome"] == "no_action" and ex["outcome"] == "no_action"
    n_ans = sum(c["expected"]["outcome"] in ("act", "no_action", "refuse") for c in d["cases"])
    assert d["scorecard"]["n_answered"] == n_ans
    assert d["scorecard"]["false_abstain"]["n_should_answer"] == sum(
        c["expected"]["outcome"] in ("act", "no_action") for c in d["cases"]
    )


def test_confident_on_abstain_is_never_double_counted():
    c = {
        "expected": {"outcome": "abstain"},
        "wrong_confident": True,
        "hard_zero_flags": ["confident_on_abstain"],
    }
    assert export_rules.derived_flags(c) == ["confident_on_abstain"]
    c["hard_zero_flags"] = []
    assert export_rules.derived_flags(c) == ["confident_on_abstain"]
    run = evalcore.CaseRun(
        case_id="x-a-001", expected="abstain", answered=True, allowed=False,
        score={"correct": False, "wrong_confident": True, "hard_zero_flags": ["confident_on_abstain"]},
    )  # fmt: skip
    sc = evalcore.build_scorecard(
        [run], lock=evalcore.Lock(files={}), lock_sha256="a" * 64, hard_zero_defs=[], split="test"
    )
    assert sc["hard_zeros"]["confident_on_abstain"] == 1


def test_chain_two_covers_the_tenant():
    d = example("refunds")
    assert d["audit"]["chain"] == chain.CHAIN_2
    d["audit"]["events"][0]["tenant_id"] = "elsewhere"
    assert any("audit chain does not verify" in m for m in messages(d))
    # and a chain/1 recipe over the same events does not match (the formats are different on purpose)
    assert chain.verify(example("refunds")["audit"]["events"], chain.CHAIN)["ok"] is False
