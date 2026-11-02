"""Review 3: vendoring bound to the bytes, raw hashes, tamper gaps, kitcheck robustness.

Every test here fails on head 73dbc86."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hadavin_kit import export_rules, kitcheck, kitsum, paths, rules_v11, textnorm
from hadavin_kit.testing import KIT, example

sys.path.insert(0, str(KIT / "console"))
sys.path.insert(0, str(KIT / "tools"))
import build_console

import vendor

COMMIT = "b" * 40


def vend(tmp_path, commit=COMMIT):
    demo = tmp_path / "demo"
    assert vendor.cmd_copy(demo, commit) == 0
    return demo, demo / "vendor" / "portal-kit", demo / "vendor" / "KIT.lock"


def rewrite_lock(lock_path, fn):
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    fn(data)
    lock_path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- M1: the lock describes the bytes that were copied
def test_a_re_vendor_leaves_no_stale_or_planted_file(tmp_path):
    demo, vdir, lock = vend(tmp_path)
    stale = vdir / "hadavin_kit" / "stale.py"
    stale.write_text("x = 1\n", encoding="utf-8")
    assert any("stale.py" in m for m in kitsum.check(vdir, lock))
    assert vendor.cmd_copy(demo, COMMIT) == 0
    assert not stale.exists()
    assert "stale.py" not in json.loads(lock.read_text(encoding="utf-8"))["files"]
    assert kitsum.check(vdir, lock) == []


def test_the_lock_hashes_the_source_not_the_copy(tmp_path, monkeypatch):
    real = shutil.copyfile

    def corrupting(src, dst, *a, **k):
        out = real(src, dst, *a, **k)
        if str(dst).endswith("chain.py"):
            Path(dst).write_bytes(b"# not the kit's file\n")
        return out

    monkeypatch.setattr(vendor.shutil, "copyfile", corrupting)
    assert vendor.cmd_copy(tmp_path / "demo", COMMIT) == 1  # the post-copy check finds it


def make_git_kit(tmp_path):
    """A throw-away git repository shaped like this one (runtime files + pyproject.toml) with origin/main at its commit."""
    repo = tmp_path / "repo"
    kit = repo
    for rel in kitsum.runtime_files(KIT):
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT / rel, kit / rel)
    shutil.copyfile(KIT / "pyproject.toml", repo / "pyproject.toml")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Hadavin Dev",
        "GIT_AUTHOR_EMAIL": "dev@hadavin.com",
        "GIT_COMMITTER_NAME": "Hadavin Dev",
        "GIT_COMMITTER_EMAIL": "dev@hadavin.com",
    }

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
            capture_output=True, text=True, check=True, env=env, timeout=60,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "kit")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    return repo, kit, head, git


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_copy_binds_the_commit_to_head_and_a_clean_tree(tmp_path, monkeypatch):
    _repo, kit, head, git = make_git_kit(tmp_path)
    monkeypatch.setattr(vendor, "KIT", kit)
    monkeypatch.delenv("HADAVIN_VENDOR_ALLOW_UNMERGED")
    assert vendor.cmd_copy(tmp_path / "ok", head) == 0
    assert vendor.cmd_copy(tmp_path / "wrong", "1" * 40) == 2  # not HEAD
    (kit / "console" / "app.css").write_text("/* local edit */\n", encoding="utf-8")
    assert (
        vendor.cmd_copy(tmp_path / "dirty", head) == 2
    )  # HEAD, but the files are not the commit's bytes
    assert not (tmp_path / "dirty").exists()
    git("checkout", "--", ".")
    (kit / "console" / "untracked.js").write_text("x\n", encoding="utf-8")
    assert vendor.cmd_copy(tmp_path / "untracked", head) == 2
    (kit / "console" / "untracked.js").unlink()
    git("update-ref", "-d", "refs/remotes/origin/main")
    assert vendor.cmd_copy(tmp_path / "unmerged", head) == 2  # HEAD, clean, but not on origin/main


def test_merged_fails_closed_without_a_git_checkout(tmp_path, monkeypatch):
    bare = tmp_path / "exported" / "portal-kit"
    bare.mkdir(parents=True)
    monkeypatch.setattr(vendor, "KIT", bare)
    assert vendor.merged(COMMIT) is False
    assert "not a git checkout" in (vendor.source_problem(COMMIT) or "")


def test_the_unmerged_escape_is_an_environment_variable_not_a_flag(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        vendor.main(["copy", "somewhere", "--commit", COMMIT, "--allow-unmerged"])
    assert "--allow-unmerged" not in vendor.__doc__


def test_pins_fail_when_the_pyproject_is_missing(tmp_path, monkeypatch):
    kit = tmp_path / "x" / "portal-kit"
    kit.mkdir(parents=True)
    monkeypatch.setattr(vendor, "KIT", kit)
    with pytest.raises(RuntimeError):
        vendor.pins()
    assert vendor.cmd_copy(tmp_path / "demo", COMMIT) == 2


def test_the_lock_records_a_minimum_range_for_jsonschema(tmp_path):
    _, _, lock = vend(tmp_path)
    req = json.loads(lock.read_text(encoding="utf-8"))["requires"]
    assert req["jsonschema_range"] == ">=4.26.0,<5" and req["jsonschema"].startswith("jsonschema")


def test_a_version_outside_the_declared_range_is_a_note():
    assert kitcheck._range_note("4.25.9", ">=4.26.0,<5")
    assert kitcheck._range_note("5.0.0", ">=4.26.0,<5")
    assert kitcheck._range_note("4.26.0", ">=4.26.0,<5") is None
    assert kitcheck._range_note("4.30.1", ">=4.26.0,<5") is None


# ---------------------------------------------------------------- M2: the hash is over RAW bytes
def test_a_crlf_rewrite_of_a_vendored_file_is_found(tmp_path):
    _, vdir, lock = vend(tmp_path)
    f = vdir / "console" / "app.js"
    f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    got = " | ".join(kitsum.check(vdir, lock))
    assert "console/app.js: changed since it was vendored" in got


def test_the_hash_is_the_plain_sha256_of_the_bytes(tmp_path):
    import hashlib

    f = tmp_path / "f.txt"
    f.write_bytes(b"a\r\nb\n")
    assert kitsum._sha(f) == hashlib.sha256(b"a\r\nb\n").hexdigest()


# ---------------------------------------------------------------- M3: tamper coverage
@pytest.mark.parametrize(
    "rel", ["hadavin_kit/docgen.py", "hadavin_kit/testing.py", "hadavin_kit/asks.v1.json"]
)
def test_a_never_vendored_development_file_in_the_vendor_dir_is_unlisted(tmp_path, rel):
    _, vdir, lock = vend(tmp_path)
    (vdir / rel).write_text("planted\n", encoding="utf-8")
    assert f"{rel}: not in the lock (unlisted file)" in " | ".join(kitsum.check(vdir, lock))


@pytest.mark.parametrize("rel", ["sitecustomize.py", "extra/x.py", "conftest.py"])
def test_a_file_at_the_vendor_root_or_in_another_directory_is_unlisted(tmp_path, rel):
    _, vdir, lock = vend(tmp_path)
    (vdir / rel).parent.mkdir(parents=True, exist_ok=True)
    (vdir / rel).write_text("planted\n", encoding="utf-8")
    assert f"{rel}: not in the lock (unlisted file)" in " | ".join(kitsum.check(vdir, lock))


def test_os_noise_files_are_not_unlisted(tmp_path):
    _, vdir, lock = vend(tmp_path)
    (vdir / ".DS_Store").write_bytes(b"x")
    (vdir / "hadavin_kit" / "__pycache__").mkdir()
    (vdir / "hadavin_kit" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
    assert kitsum.check(vdir, lock) == []


@pytest.mark.parametrize("bad", ["../elsewhere", "/abs/path", "vendor/../../x", "C:/x", "a\\b"])
def test_a_vendor_dir_that_leaves_the_demo_is_refused(tmp_path, bad, capsys):
    demo, _, lock = vend(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(
        demo / "vendor" / "portal-kit", elsewhere
    )  # a perfectly clean copy outside the demo
    rewrite_lock(lock, lambda d: d.update(vendor_dir=bad))
    assert kitsum.main(["--check", str(demo)]) == 1
    assert "vendor_dir" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.clear(),
        lambda d: d.update(files=[]),
        lambda d: d.update(files={"hadavin_kit/chain.py": 5}),
        lambda d: d.update(files={"../x": "a" * 64}),
        lambda d: d.update(files={"hadavin_kit/chain.py": "G" * 64}),
    ],
)
def test_a_malformed_lock_is_reported_not_raised(tmp_path, mutate, capsys):
    demo, vdir, lock = vend(tmp_path)
    rewrite_lock(lock, mutate)
    assert kitsum.check(vdir, lock)
    assert kitsum.main(["--check", str(demo)]) == 1
    lock.write_text("[1, 2]", encoding="utf-8")
    assert kitsum.check(vdir, lock) and kitsum.main(["--check", str(demo)]) == 1


def test_hadavin_kit_root_is_a_problem_under_kit_lock(tmp_path, monkeypatch):
    doc = example("askdata")
    doc["demo"]["kit_commit"] = COMMIT
    lock = tmp_path / "KIT.lock"
    lock.write_text(json.dumps({"kit_commit": COMMIT, **kitsum.lock_data(KIT)}), encoding="utf-8")
    assert not [p for p in kitcheck._lock_problems(doc, lock) if p.path == "HADAVIN_KIT_ROOT"]
    monkeypatch.setattr(paths, "OVERRIDDEN", True)
    assert [p for p in kitcheck._lock_problems(doc, lock) if p.path == "HADAVIN_KIT_ROOT"]


def test_paths_no_longer_claim_a_lock_location():
    assert not hasattr(paths, "LOCK")


# ---------------------------------------------------------------- M4: kitcheck does not crash, fail open or miss folded terms
def test_a_shape_broken_export_beside_a_page_is_a_problem_not_a_traceback(tmp_path):
    doc = example("refunds")
    d = tmp_path / "e"
    d.mkdir()
    (d / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    (d / "index.html").write_text(build_console.build(doc), encoding="utf-8")
    broken = dict(doc)
    broken["demo"] = {"slug": "refunds"}  # no title, no kit_commit...
    (d / "export.json").write_text(json.dumps(broken), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(KIT)}
    res = subprocess.run(
        [sys.executable, "-m", "hadavin_kit.kitcheck", str(d), "--json"],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )  # fmt: skip
    assert res.returncode == 1 and "Traceback" not in res.stderr, res.stderr
    out = json.loads(res.stdout)
    assert out["ok"] is False and out["problems"]


def report_dir(tmp_path, report_text):
    doc = example("askdata")
    doc["ledger"].update(scope="load_run")
    d = tmp_path / "r"
    d.mkdir()
    (d / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    (d / "report.json").write_text(report_text, encoding="utf-8")
    return d


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', '{"a": NaN}', "\u00ff not json"])
def test_a_report_that_is_not_strict_json_is_a_problem_under_deploy_and_a_note_otherwise(
    tmp_path, text
):
    d = report_dir(tmp_path, text)
    plain = kitcheck.run(d)
    assert not [
        p for p in plain["problems"] if p.path == "report.json" and p.code == "REPORT_MISMATCH"
    ]
    assert any("report.json is not strict JSON" in n for n in plain["notes"])
    for kw in ({"require_report": True}, {"deploy": True}):
        got = kitcheck.run(d, **kw)
        assert any(
            p.path == "report.json" and p.code == "REPORT_MISMATCH" for p in got["problems"]
        ), kw


def test_a_deeply_nested_html_payload_does_not_crash_the_deny_scan(tmp_path):
    doc = example("refunds")
    d = tmp_path / "h"
    d.mkdir()
    (d / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    (d / "index.html").write_text(
        '<script id="demo-data" type="application/json">' + "[" * 5000 + "]" * 5000 + "</script>",
        encoding="utf-8",
    )
    kitcheck.run(d, deny=["zzz-nothing"])  # must return, not raise RecursionError


@pytest.mark.parametrize(
    ("planted", "term"),
    [
        ("Ac\u200bme Corp", "Acme Corp"),  # zero-width space inside the name
        ("Acme  Corp", "Acme Corp"),  # double space
        ("Acme\nCorp", "acme corp"),  # newline
        ("Ac\u00adme Corp", "ACME CORP"),  # soft hyphen
        ("Acme\u200d Corp", "acme corp"),
    ],
)
def test_the_deny_scan_sees_through_format_characters_and_whitespace(tmp_path, planted, term):
    d = report_dir(tmp_path, json.dumps({"note": planted}, ensure_ascii=False))
    got = kitcheck.run(d, deny=[term])
    assert any(p.code == "DENYLIST_HIT" for p in got["problems"]), repr(planted)


def test_fold_removes_control_and_format_characters_and_collapses_whitespace():
    assert textnorm.fold("A\u200bB\u2060C\ufeff") == "abc"
    assert textnorm.fold("a \t\n b") == "a b"
    assert textnorm.fold("\ufeffMÜLLER") == textnorm.fold("müller")


@pytest.mark.parametrize(
    "name",
    ["amounts", "fees", "payments", "charges", "prices", "totals", "amount2", "total_1", "fee3_usd", "totalusd",
     "refundamount", "amt", "feeusd", "unit_price", "subtotal", "net_amts", "balances"],
)  # fmt: skip
def test_money_names_with_plurals_digits_and_concatenations(name):
    assert export_rules.is_money_name(name), name


@pytest.mark.parametrize(
    "name",
    [
        "account_id",
        "account",
        "value",
        "cost",
        "cost_center",
        "accounting",
        "values",
        "thousand",
        "amateur",
        "status",
    ],
)
def test_bare_value_account_and_cost_are_not_money(name):
    assert not export_rules.is_money_name(name), name


def test_roles_mark_must_end_the_summary_exactly():
    assert rules_v11.ROLES_MARK.search("approved [roles=a,b]")
    assert rules_v11.ROLES_MARK.search("approved [roles=a]\n") is None
    assert rules_v11.ROLES_MARK.search("approved [roles=a] ") is None


def test_the_roles_grammar_scope_is_written_down():
    assert "ANY approval event" in rules_v11._roles_grammar.__doc__


# ---------------------------------------------------------------- M5 / M1: the harness covers the copy tool and survives a slow host
import mutation


def test_the_copy_tool_is_a_mutation_target():
    assert "tools/vendor.py" in mutation.DEFAULT_TARGETS
    ms = mutation.enumerate_python("tools/vendor.py")
    assert len(ms) > 20
    original = (KIT / "tools" / "vendor.py").read_text(encoding="utf-8")
    assert all(mutation.mutate_python("tools/vendor.py", m.index) != original for m in ms[:25])
    assert "test_review4.py" in mutation.TEST_FILES


def test_a_baseline_that_times_out_is_reported_not_raised(monkeypatch, capsys):
    def slow(work, first=()):
        raise subprocess.TimeoutExpired("pytest", 1)

    monkeypatch.setattr(mutation, "run_tests", slow)
    monkeypatch.setattr(mutation, "make_workspace", lambda dst: dst)
    assert mutation.main(["--limit", "1"]) == 3
    assert "BASELINE IS NOT GREEN" in capsys.readouterr().out


def test_the_killing_test_files_of_a_target_run_first(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(mutation.subprocess, "run", fake)
    mutation.run_tests(KIT, mutation.FIRST["console/build_console.py"])
    files = [a for a in seen["cmd"] if a.endswith(".py") and "tests/" in a]
    assert files[0].endswith("test_vendor.py") and len(files) == len(set(files))
    assert len(files) == len([n for n in mutation.TEST_FILES if (KIT / "tests" / n).exists()])


def test_a_report_that_overflows_the_parser_stack_is_a_problem_under_deploy(tmp_path, monkeypatch):
    # the real depth that overflows differs by platform, so the overflow is injected
    d = report_dir(tmp_path, "{}")
    real = kitcheck.strict_json.loads

    def overflow(text):
        if text.startswith("{}"):
            raise RecursionError
        return real(text)

    monkeypatch.setattr(kitcheck.strict_json, "loads", overflow)
    got = kitcheck.run(d, require_report=True)
    assert any(p.path == "report.json" and p.code == "REPORT_MISMATCH" for p in got["problems"])


@pytest.mark.parametrize("rel", ["/abs/x.py", "", "a//b/../c", "a" + chr(92) + "b", "c:x"])
def test_an_unsafe_path_in_a_lock_is_reported(tmp_path, rel):
    _, vdir, lock = vend(tmp_path)
    rewrite_lock(lock, lambda d: d["files"].update({rel: "a" * 64}))
    assert any("safe relative path" in m for m in kitsum.check(vdir, lock))


def _stub_run(monkeypatch):
    monkeypatch.setattr(
        mutation, "run_tests", lambda work, first=(): subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(mutation, "make_workspace", lambda dst: dst)
    monkeypatch.setattr(mutation, "canary_checks", lambda: 0)
    monkeypatch.setattr(mutation, "run_mutant", lambda code, rel: "killed")


def test_a_mutant_that_cannot_be_built_is_an_error_line_not_a_crash(monkeypatch, capsys):
    _stub_run(monkeypatch)

    def boom(rel, index):
        raise IndexError

    monkeypatch.setattr(mutation, "mutate_python", boom)
    assert mutation.main(["--limit", "3", "--workers", "1", "--targets", "chain.py"]) == 1
    assert "ERROR (mutant could not be built)" in capsys.readouterr().out


def test_the_run_uses_a_snapshot_so_edits_mid_run_cannot_shift_the_mutant_list(monkeypatch):
    _stub_run(monkeypatch)
    live = mutation.KIT
    seen = {}

    def spy(rel, index):
        seen["kit"] = mutation.KIT
        return (mutation.KIT / "hadavin_kit" / "chain.py").read_text(encoding="utf-8")

    monkeypatch.setattr(mutation, "mutate_python", spy)
    mutation.main(["--limit", "1", "--workers", "1", "--targets", "chain.py"])
    assert seen["kit"] != live and mutation.KIT == live  # run on a copy, global restored
