"""Boundaries of the evaluation gate and the kitcheck odds and ends that the full mutation run (all mutants) left alive."""

import copy
from decimal import Decimal

import pytest
from hadavin_kit import evalcore, kitcheck, validate
from hadavin_kit.evalcore import CaseRun, Lock, LockError
from hadavin_kit.testing import example

TS = evalcore.Thresholds


def run(case_id, expected, answered, *, correct=True, wrong=False, flags=(), held=False):
    return CaseRun(
        case_id=case_id, expected=expected, answered=answered, allowed=False,
        score={"correct": correct, "wrong_confident": wrong, "hard_zero_flags": list(flags)}, held=held,
    )  # fmt: skip


# ----------------------------------------------------------------------------- sealed gate, thresholds, lock
def test_sealed_gate_allows_exactly_the_minimum_remaining_budget():
    lock = Lock(files={})
    evalcore.sealed_gate(lock, Decimal("3.00"), split="test", second_look=None)
    with pytest.raises(LockError, match="needs at least"):
        evalcore.sealed_gate(lock, Decimal("2.99"), split="test", second_look=None)
    evalcore.sealed_gate(lock, Decimal(0), split="dev", second_look=None)  # dev is never gated


def test_verdict_thresholds_are_inclusive():
    bound = evalcore.upper_error_bound(0, 30)
    d = Decimal(str(bound))
    tiny = Decimal("0.0001")
    assert evalcore.verdict_for(0, 30, 0, TS(d, d))[0] == "green"
    assert evalcore.verdict_for(0, 30, 0, TS(d - tiny, d))[0] == "amber"
    assert evalcore.verdict_for(0, 30, 0, TS(d - tiny, d - tiny))[0] == "not_demonstrated"
    assert (
        evalcore.verdict_for(0, 30, 1, TS(d, d))[0] == "not_demonstrated"
    )  # a hard zero beats any bound


def test_green_threshold_may_equal_but_not_exceed_amber():
    assert TS(Decimal("0.1"), Decimal("0.1"))
    with pytest.raises(LockError, match="must not exceed"):
        TS(Decimal("0.11"), Decimal("0.1"))
    for bad in (Decimal("-0.01"), Decimal("1.01"), Decimal("NaN")):
        with pytest.raises(LockError):
            TS(bad, Decimal("0.5"))


def test_lock_from_json_rejects_each_malformed_field():
    ok = {"files": {}}
    assert Lock.from_json(ok)
    cases = [
        {**ok, "thresholds": []},
        {**ok, "thresholds": {"green_max_upper": "0.1", "surprise": "1"}},
        {**ok, "thresholds": {"surprise": "1"}},
        {**ok, "run_ids": "abc"},
        {**ok, "run_ids": [1, 2]},
        {**ok, "run_ids": ["a", 2]},
        {**ok, "sealed_min_remaining_usd": "-1"},
        {**ok, "sealed_min_remaining_usd": "NaN"},
        {**ok, "test_runs": -1},
        {**ok, "test_runs": True},
        {**ok, "last_test_status": "mystery"},
        {**ok, "unknown_key": 1},
    ]
    for bad in cases:
        with pytest.raises(LockError):
            Lock.from_json(bad)
    assert Lock.from_json({**ok, "sealed_min_remaining_usd": "0"}).sealed_min_remaining_usd == 0


def test_record_run_counts_a_harness_defect_look_only_for_that_reason(tmp_path):
    evalcore.write_lock(tmp_path, Lock(files={}, test_runs=1, last_test_status="aborted"))
    after = evalcore.record_run(
        tmp_path, "r-2", split="test", status="complete", second_look="harness_defect"
    )
    assert after.harness_defect_looks == 1 and after.test_runs == 2
    again = evalcore.record_run(
        tmp_path, "r-3", split="test", status="complete", second_look="aborted"
    )
    assert (
        again.harness_defect_looks == 1 and again.test_runs == 3
    )  # an aborted second look is not a defect look


# ----------------------------------------------------------------------------- scorecard numbers pinned
def scorecard(runs):
    return evalcore.build_scorecard(
        runs, lock=Lock(files={}), lock_sha256="a" * 64, hard_zero_defs=[], split="test"
    )


def test_abstention_coverage_and_refusal_numbers_are_exact():
    runs = [
        run("x-e-001", "act", True),  # answered, correct
        run("x-e-002", "act", False, correct=False),  # false abstain
        run("x-a-001", "abstain", False),  # handed back, right
        run("x-a-002", "abstain", True, correct=False, wrong=True),  # confident on abstain
        run("x-t-001", "refuse", False),  # refused, right
        run("x-t-002", "refuse", False),  # refused too
        run(
            "x-e-003", "act", True, correct=False
        ),  # answered, but wrong (not a confident-wrong flag)
    ]
    sc = scorecard(runs)
    assert sc["n_refused"] == 2 and sc["n"] == 7 and sc["n_answered"] == 5
    assert sc["coverage"] == {"answered_correct": 1, "definite": 3}
    # handed back (not answered): e-002, a-001, t-001, t-002; of those, ones that should be held: a-001, t-001, t-002
    assert sc["abstention"]["precision"] == pytest.approx(3 / 4)
    # should hold: a-001, a-002, t-001, t-002; handed back among them: a-001, t-001, t-002
    assert sc["abstention"]["recall"] == pytest.approx(3 / 4)
    assert sc["false_abstain"] == {"x": 1, "n_should_answer": 3, "rate": pytest.approx(1 / 3)}
    assert sc["hard_zeros"]["confident_on_abstain"] == 1


def test_no_action_gold_counts_as_owed_and_as_a_false_abstain_domain():
    runs = [run("x-e-001", "no_action", True), run("x-e-002", "no_action", False, correct=False)]
    sc = scorecard(runs)
    assert sc["false_abstain"] == {"x": 1, "n_should_answer": 2, "rate": 0.5}
    assert sc["abstention"]["precision"] == 0.0  # the one handed back should not have been


def test_held_is_false_hold_not_false_abstain_and_only_when_a_case_is_held():
    held = [run("x-e-001", "act", False, held=True, correct=False), run("x-e-002", "act", True)]
    sc = scorecard(held)
    assert sc["false_hold"] == {"x": 1, "n": 2} and sc["false_abstain"]["x"] == 0
    assert "false_hold" not in scorecard([run("x-e-001", "act", True)])


def test_informational_flags_do_not_count_toward_a_regression():
    def card(n_info):
        sc = scorecard([run("x-e-001", "act", True)])
        sc["hard_zeros"] = {"info_flag": n_info}
        return sc

    a, b = card(0), card(1)
    quiet = evalcore.compare_scorecards(a, b, informational_flags=frozenset({"info_flag"}))
    loud = evalcore.compare_scorecards(a, b)
    assert quiet["regression"] is False and loud["regression"] is True


def test_report_prints_na_for_a_missing_bound_and_a_percentage_otherwise():
    sc = scorecard([run("x-e-001", "act", True)])
    assert "n/a" not in evalcore.render_md("t", sc) or sc["confident_wrong"]["upper_bound"] is None
    none = copy.deepcopy(sc)
    none["confident_wrong"]["upper_bound"] = None
    assert "n/a" in evalcore.render_md("t", none)
    some = copy.deepcopy(sc)
    some["confident_wrong"]["upper_bound"] = 0.123
    assert "12.3 %" in evalcore.render_md("t", some)


# ----------------------------------------------------------------------------- kitcheck odds and ends
def nested(n, leaf):
    cur = leaf
    for _ in range(n):
        cur = {"k": cur}
    return cur


def test_decoded_string_collection_stops_at_its_depth_limit_exactly():
    assert "deep" in kitcheck._strings(
        nested(60, "deep")
    )  # the string sits at depth 60: still collected
    assert "deep" not in kitcheck._strings(nested(61, "deep"))
    assert kitcheck._strings({"a": ["x", {"b": "y"}], 3: 4}) == ["a", "x", "b", "y", "3"]


def write_one(tmp_path, name, doc):
    import json

    root = tmp_path / name
    root.mkdir()
    (root / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_test_double_is_recognised_by_its_model_alone_and_by_its_note_alone(tmp_path):
    by_model = example("askdata")
    by_model["run"]["model"] = kitcheck.TEST_DOUBLE_MODEL
    by_model["run"]["mode"] = "live"
    by_model["scorecard"]["scripted"] = False
    by_model["scorecard"]["verdict"] = "amber"
    assert any(
        "test double" in p.message
        for p in kitcheck.run(write_one(tmp_path, "m", by_model))["problems"]
    )
    by_note = example("askdata")
    by_note["honesty"]["notes"] = ["TEST DOUBLE: x"]
    assert any(
        "test double" in p.message
        for p in kitcheck.run(write_one(tmp_path, "n", by_note))["problems"]
    )
    plain = kitcheck.run(write_one(tmp_path, "p", example("askdata")))
    assert not any("test double" in p.message for p in plain["problems"])


def test_empty_notes_list_does_not_crash_kitcheck(tmp_path):
    d = example("askdata")
    d["honesty"]["notes"] = []
    res = kitcheck.run(write_one(tmp_path, "e", d))
    assert validate.validate(d) == [] and res["ok"]
