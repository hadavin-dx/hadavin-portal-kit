"""The mutation harness itself (review M6): what counts as a kill, how mutants are keyed, which targets exist."""

import ast
import json
import subprocess
import sys

import pytest
from hadavin_kit.testing import KIT

sys.path.insert(0, str(KIT / "tools"))
import mutation


@pytest.mark.parametrize(
    ("rc", "word"),
    [
        (0, "survived"),
        (1, "killed"),
        (2, "inconclusive"),
        (3, "inconclusive"),
        (4, "inconclusive"),
        (5, "inconclusive"),
        (-9, "inconclusive"),
    ],
)
def test_only_pytest_exit_code_one_is_a_kill(rc, word):
    assert mutation.verdict_of(rc) == word


def test_targets_include_the_loader_codes_evalcore_and_the_schema():
    assert {"strict_json.py", "problems.py", "evalcore.py", mutation.SCHEMA_REL} <= set(
        mutation.DEFAULT_TARGETS
    )
    assert "test_hostile.py" in mutation.TEST_FILES and "test_schema_walk.py" in mutation.TEST_FILES


def test_keys_carry_the_function_and_full_text_not_a_line_or_a_truncation():
    ms = mutation.enumerate_python("export_rules.py")
    assert ms and all(m.func and "::" in m.key for m in ms)
    longest = max(ms, key=lambda m: len(m.text))
    assert len(longest.text) > 100 and longest.text in longest.key
    assert all(str(m.line) not in m.key.split("::")[0] for m in ms)


def test_new_operators_exist_and_really_change_the_source():
    kinds = set()
    for t in ("export_rules.py", "validate.py", "kitcheck.py", "evalcore.py", "versioning.py"):
        kinds |= {m.kind for m in mutation.enumerate_python(t)}
    assert {"boundary literal +1", "return bool flipped", "rule deleted", "not removed"} <= kinds
    for t in ("export_rules.py", "validate.py"):
        original = ast.unparse(ast.parse((KIT / "hadavin_kit" / t).read_text(encoding="utf-8")))
        for m in mutation.enumerate_python(t)[:60]:
            assert mutation.mutate_python(t, m.index) != original, (t, m.kind, m.text[:40])


def test_schema_mutants_apply_and_change_the_schema():
    ms = mutation.enumerate_schema()
    assert len(ms) > 100
    original = json.loads((KIT / mutation.SCHEMA_REL).read_text(encoding="utf-8"))
    kinds = {m.kind.split(" -")[0] for m in ms}
    assert {
        "schema additionalProperties false->true",
        "schema required",
        "schema enum",
        "schema pattern removed",
    } <= kinds
    for m in ms[:: max(1, len(ms) // 40)]:
        assert json.loads(mutation.mutate_schema(m.index)) != original, m.kind


def test_the_fixed_survivor_report_format_round_trips_through_only(tmp_path, monkeypatch, capsys):
    """`--only` must not turn on --all: an equivalents entry outside the re-run set is NOT stale (it was, before)."""
    ms = mutation.enumerate_python("chain.py")
    pick = ms[0]
    report = tmp_path / "r.txt"
    report.write_text(
        f"  SURVIVOR {pick.file} [{pick.kind}] ({pick.func}) {pick.text}\n", encoding="utf-8"
    )
    eq = tmp_path / "eq.json"
    eq.write_text(
        json.dumps({"equivalent": [{"key": "chain.py::nowhere::cmp Eq->NotEq::x", "reason": "r"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mutation, "EQUIVALENTS", eq)
    monkeypatch.setattr(
        mutation, "run_tests", lambda work: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(mutation, "make_workspace", lambda dst: dst)
    monkeypatch.setattr(mutation, "canary_checks", lambda: 0)
    monkeypatch.setattr(mutation, "run_mutant", lambda code, rel: "killed")
    assert mutation.main(["--only", str(report), "--workers", "1", "--targets", "chain.py"]) == 0
    out = capsys.readouterr().out
    assert "1 run of" in out and "STALE" not in out
    # and with --all the stale entry IS reported
    assert mutation.main(["--all", "--workers", "1", "--targets", "chain.py"]) == 1
    assert "STALE" in capsys.readouterr().out


def test_inconclusive_mutants_fail_the_run(tmp_path, monkeypatch, capsys):
    eq = tmp_path / "eq.json"
    eq.write_text(json.dumps({"equivalent": []}), encoding="utf-8")
    monkeypatch.setattr(mutation, "EQUIVALENTS", eq)
    monkeypatch.setattr(
        mutation, "run_tests", lambda work: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(mutation, "make_workspace", lambda dst: dst)
    monkeypatch.setattr(mutation, "canary_checks", lambda: 0)
    monkeypatch.setattr(mutation, "run_mutant", lambda code, rel: "inconclusive")
    assert mutation.main(["--limit", "3", "--workers", "1", "--targets", "chain.py"]) == 1
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_a_red_baseline_stops_the_run_and_a_blind_harness_is_detected(monkeypatch, capsys):
    monkeypatch.setattr(mutation, "make_workspace", lambda dst: dst)
    monkeypatch.setattr(
        mutation, "run_tests", lambda work: subprocess.CompletedProcess([], 1, "red", "")
    )
    assert mutation.main(["--limit", "1", "--targets", "chain.py"]) == 3
    monkeypatch.setattr(
        mutation, "run_tests", lambda work: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(mutation, "run_mutant", lambda code, rel: "survived")
    assert mutation.canary_checks() == 4  # the deliberate break survived: blind
    monkeypatch.setattr(mutation, "run_mutant", lambda code, rel: "killed")
    assert mutation.canary_checks() == 5  # the no-op was "killed": flaky
    capsys.readouterr()


def test_ci_varies_the_seed_and_a_manual_job_runs_everything():
    ci = (KIT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--seed" in ci and "run_id" in ci and "workflow_dispatch" in ci and "--all" in ci
