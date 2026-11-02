"""Branches the full mutation run of round 3 left alive: one test per class of survivor."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest
from hadavin_kit import chain, evalcore, export_rules, honest, kitcheck, kitsum, validate
from hadavin_kit.evalcore import Lock, LockError
from hadavin_kit.testing import KIT, example

sys.path.insert(0, str(KIT / "console"))
sys.path.insert(0, str(KIT / "tools"))
import build_console

import vendor


def codes(doc):
    return {p.code for p in validate.validate(doc)}


def write_dir(root, doc, *, page=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    if page:
        (root / "index.html").write_text(build_console.build(doc), encoding="utf-8")
        _, css, js = build_console.assets()
        (root / "_headers").write_text(
            build_console.headers_file(build_console.csp_for(js, css)), encoding="utf-8"
        )
    return root


# ---------------------------------------------------------------- validate: which limit is which
def test_size_limits_are_classified_oversize_each_by_its_own_keyword():
    d = example("refunds")
    d["cases"][0]["steps"] = [d["cases"][0]["steps"][0]] * 101
    got = [p for p in validate.validate(d) if p.code == "OVERSIZE"]
    assert any("maxItems exceeded" in p.message for p in got)
    d = example("refunds")
    d["approvals"][0]["args"] = {f"k{i}": 1 for i in range(65)}
    assert any(
        "maxProperties exceeded" in p.message for p in validate.validate(d) if p.code == "OVERSIZE"
    )
    d = example("refunds")
    d["cases"][0]["input_summary"] = "x" * 2001
    assert any(
        "maxLength exceeded" in p.message for p in validate.validate(d) if p.code == "OVERSIZE"
    )


# ---------------------------------------------------------------- honest: each field of a false chain claim
@pytest.mark.parametrize("field", ["events", "matched", "ok"])
def test_a_false_claim_in_any_one_field_is_flagged(field):
    d = example("refunds")
    claim = d["audit"]["verify"]
    claim[field] = (not claim[field]) if field == "ok" else claim[field] + 1
    assert "chain_bad" in honest.status_ids(d)


# ---------------------------------------------------------------- kitcheck
@pytest.mark.parametrize(
    "snippet", ["<p>//cdn.example.org/x.css</p>", "<p>@import</p>", "<p>url(x)</p>"]
)
def test_each_remote_pattern_is_found_in_the_skeleton(tmp_path, snippet):
    root = write_dir(tmp_path / "r", example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(
        page.replace("<body>", "<body>" + snippet, 1), encoding="utf-8"
    )
    assert any("remote URL" in p.message for p in kitcheck.run(root)["problems"])


def test_load_profile_report_hash_is_checked_like_the_ledgers(tmp_path):
    doc = example("refunds")
    doc["load_profile"]["report_sha256"] = hashlib.sha256(b"report").hexdigest()
    root = write_dir(tmp_path / "lp", doc, page=False)
    assert any("load_profile" in n for n in kitcheck.run(root)["notes"])
    (root / "report.json").write_bytes(b"report")
    assert kitcheck.run(root)["ok"]
    (root / "report.json").write_bytes(b"other")
    assert any(
        p.code == "REPORT_MISMATCH" and p.path.startswith("load_profile")
        for p in kitcheck.run(root)["problems"]
    )


def test_deny_list_notes_an_export_that_is_not_strict_json(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    (root / "export.json").write_text('{"a": 1, "a": 2}', encoding="utf-8")
    res = kitcheck.run(root, deny=["zzz"])
    assert any("export.json is not strict JSON" in n for n in res["notes"])


def test_deny_list_reads_markdown_files_too(tmp_path):
    root = write_dir(tmp_path / "md", example("refunds"), page=False)
    (root / "report.md").write_text("A line naming Fabrikam Holdings", encoding="utf-8")
    assert "DENYLIST_HIT" in {
        p.code for p in kitcheck.run(root, deny=["fabrikam holdings"])["problems"]
    }


def run_cli(args, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(KIT)}
    return subprocess.run(
        [sys.executable, "-m", "hadavin_kit.kitcheck", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False
    )  # fmt: skip


def test_deploy_mode_on_the_command_line_requires_the_named_report(tmp_path):
    doc = example("askdata")
    doc["ledger"].update(scope="load_run", report_sha256="c" * 64)
    doc["demo"]["kit_commit"] = "a" * 40
    root = write_dir(tmp_path / "dep", doc, page=False)
    lock = tmp_path / "KIT.lock"
    lock.write_text(
        json.dumps({"kit_commit": "a" * 40, "schema_minor_max": 1, **kitsum.lock_data(KIT)}),
        encoding="utf-8",
    )
    head = doc["audit"]["events"][-1]["sha256"]
    deploy = run_cli(
        [str(root), "--deploy", "--expect-head", head, "--kit-lock", str(lock)], tmp_path
    )
    assert deploy.returncode == 1 and "MISSING_FILE" in deploy.stdout
    plain = run_cli([str(root), "--expect-head", head, "--kit-lock", str(lock)], tmp_path)
    assert plain.returncode == 0, plain.stdout
    needed = run_cli([str(root), "--require-report"], tmp_path)
    assert needed.returncode == 1 and "MISSING_FILE" in needed.stdout


def test_shape_problems_stop_the_honest_status_and_chain_head_whatever_their_kind(tmp_path):
    missing = example("refunds")
    del missing["registry"]
    wrong = example("refunds")
    wrong["run"] = []
    for name, doc in (("missing", missing), ("wrong", wrong)):
        res = kitcheck.run(write_dir(tmp_path / name, doc, page=False))
        assert res["chain"] is None and res["honest"] == [], name


# ---------------------------------------------------------------- kitsum: the sums-file mode and the default vendor dir
def copy_runtime(tmp_path):
    root = tmp_path / "kitcopy"
    for rel in kitsum.runtime_files(KIT):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT / rel, root / rel)
    (root / "KIT-SHA256SUMS").write_text(kitsum.sums_text(kitsum.digests(root)), encoding="utf-8")
    return root


def test_sums_mode_finds_changed_missing_and_extra_runtime_files(tmp_path):
    root = copy_runtime(tmp_path)
    assert kitsum.check(root) == []
    (root / "hadavin_kit" / "chain.py").write_text("# edited\n", encoding="utf-8")
    (root / "console" / "app.css").unlink()
    (root / "hadavin_kit" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    got = " | ".join(kitsum.check(root))
    assert "hadavin_kit/chain.py: changed since the sums were written" in got
    assert "console/app.css: missing" in got
    assert "hadavin_kit/extra.py: not in KIT-SHA256SUMS (extra runtime file)" in got


def test_a_lock_without_vendor_dir_means_the_default_vendor_directory(tmp_path):
    demo = tmp_path / "demo"
    assert vendor.cmd_copy(demo, "a" * 40) == 0
    lock_path = demo / "vendor" / "KIT.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["vendor_dir"]
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    assert kitsum.main(["--check", str(demo)]) == 0


# ---------------------------------------------------------------- build_console.validate: which problem is a "rule"
def build_error(doc):
    with pytest.raises(ValueError) as err:
        build_console.build(doc)
    return str(err.value)


def test_rule_chain_minor_and_character_problems_are_cross_field_errors():
    dup = example("refunds")
    dup["approvals"][0]["approval_id"] = dup["approvals"][1]["approval_id"]
    assert "cross-field" in build_error(dup)
    tampered = example("refunds")
    tampered["audit"]["events"][2]["summary"] += "x"
    assert "cross-field" in build_error(tampered)
    low = example("refunds")
    low["schema_minor"] = 0
    assert "cross-field" in build_error(low)
    bidi = example("refunds")
    bidi["cases"][0]["input_summary"] = "pay " + chr(0x202E) + "evil"
    assert "cross-field" in build_error(bidi)


def test_oversize_with_and_without_a_path_is_classified_correctly(monkeypatch):
    long_list = example("refunds")
    long_list["cases"][0]["steps"] = [long_list["cases"][0]["steps"][0]] * 101
    assert "does not match" in build_error(long_list)  # a shape problem at a path
    monkeypatch.setattr(export_rules, "MAX_DOC_BYTES", 1000)
    assert "cross-field" in build_error(
        example("refunds")
    )  # the whole document is too large: no path


def test_shape_error_names_the_root_when_there_is_no_path():
    d = example("refunds")
    del d["registry"]
    assert "<root>:" in build_error(d)


@pytest.mark.parametrize("marker", ["__DATA__", "__CSP__", "__TITLE__", "__JS__", "__CSS__"])
def test_a_placeholder_in_a_console_source_is_refused(monkeypatch, marker):
    template, css, js = build_console.assets()
    monkeypatch.setattr(build_console, "assets", lambda: (template, css, js + "\n// " + marker))
    with pytest.raises(ValueError, match="template placeholder"):
        build_console.build(example("refunds"))
    monkeypatch.setattr(
        build_console, "assets", lambda: (template, css + "/* " + marker + " */", js)
    )
    with pytest.raises(ValueError, match="template placeholder"):
        build_console.build(example("refunds"))


# ---------------------------------------------------------------- evalcore
def test_lock_counters_must_be_non_negative_integers():
    ok = {"files": {}}
    for name in ("test_runs", "harness_defect_looks"):
        for bad in (-1, True, 1.5, "1"):
            with pytest.raises(LockError):
                Lock.from_json({**ok, name: bad})
        assert getattr(Lock.from_json({**ok, name: 2}), name) == 2


def test_scorecard_counts_are_pinned_for_every_expected_kind():
    def run(cid, expected, answered, correct=True):
        return evalcore.CaseRun(
            case_id=cid, expected=expected, answered=answered, allowed=False,
            score={"correct": correct, "wrong_confident": False, "hard_zero_flags": []},
        )  # fmt: skip

    lock = Lock(files={})
    only_refuse = evalcore.build_scorecard(
        [run("x-t-001", "refuse", False)],
        lock=lock,
        lock_sha256="a" * 64,
        hard_zero_defs=[],
        split="test",
    )
    assert only_refuse["n_answered"] == 1 and only_refuse["abstention"]["recall"] == 1.0
    only_no_action = evalcore.build_scorecard(
        [run("x-e-001", "no_action", True)],
        lock=lock,
        lock_sha256="a" * 64,
        hard_zero_defs=[],
        split="test",
    )
    assert (
        only_no_action["n_answered"] == 1
        and only_no_action["false_abstain"]["n_should_answer"] == 1
    )
    with_abstain = evalcore.build_scorecard(
        [run("x-a-001", "abstain", False), run("x-e-002", "act", True)],
        lock=lock,
        lock_sha256="a" * 64,
        hard_zero_defs=[],
        split="test",
    )
    assert with_abstain["n_answered"] == 1 and with_abstain["abstention"] == {
        "precision": 1.0,
        "recall": 1.0,
    }
    assert copy.deepcopy(chain.GENESIS) == "0" * 64


def test_an_unlisted_file_is_found_in_each_vendored_directory(tmp_path):
    demo = tmp_path / "demo"
    assert vendor.cmd_copy(demo, "a" * 40) == 0
    vdir = demo / "vendor" / "portal-kit"
    for rel in ("hadavin_kit/extra.py", "schema/extra.json", "console/extra.js"):
        (vdir / rel).write_text("x\n", encoding="utf-8")
    got = " | ".join(kitsum.check(vdir, demo / "vendor" / "KIT.lock"))
    for rel in ("hadavin_kit/extra.py", "schema/extra.json", "console/extra.js"):
        assert f"{rel}: not in the lock (unlisted file)" in got, rel
