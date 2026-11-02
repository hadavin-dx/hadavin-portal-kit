"""The vendoring contract (review code-2 B1, design-2 B1): vendor/KIT.lock, KIT-SHA256SUMS, the check, kitcheck --kit-lock.

The format is the one the demos' sync check verifies: KIT-SHA256SUMS in sha256sum lines; vendor/KIT.lock with
kit_commit, vendor_dir, files and tree_sha256 = sha256 of the sorted "<sha>  <path>" lines."""

import json
import os
import subprocess
import sys

import pytest
from hadavin_kit import kitcheck, kitsum, paths, testing
from hadavin_kit.testing import KIT, example

sys.path.insert(0, str(KIT / "tools"))
import vendor

COMMIT = "a" * 40

# pinned independently of the code under test: the runtime files, and only those, are vendored
RUNTIME = [
    "console/app.css", "console/app.js", "console/build_console.py", "console/chain.js", "console/index.template.html",
    "hadavin_kit/__init__.py", "hadavin_kit/chain.py", "hadavin_kit/evalcore.py", "hadavin_kit/export_rules.py",
    "hadavin_kit/honest.py", "hadavin_kit/kitcheck.py", "hadavin_kit/kitsum.py", "hadavin_kit/paths.py",
    "hadavin_kit/problems.py", "hadavin_kit/pytest_plugin.py", "hadavin_kit/rules_v11.py",
    "hadavin_kit/scripted_llm.py", "hadavin_kit/strict_json.py", "hadavin_kit/textnorm.py", "hadavin_kit/validate.py",
    "hadavin_kit/versioning.py", "schema/demo-export.v1.json",
]  # fmt: skip


def vend(tmp_path, commit=COMMIT):
    demo = tmp_path / "demo"
    assert vendor.cmd_copy(demo, commit) == 0
    return demo / "vendor" / "portal-kit"  # the vendored kit root; its lock is ../KIT.lock


def run_py(args, *, cwd, pythonpath, check=False):
    env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    env.pop("HADAVIN_KIT_ROOT", None)
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=check
    )  # fmt: skip


def test_runtime_file_list_is_exactly_the_pinned_one():
    assert kitsum.runtime_files(KIT) == RUNTIME
    assert "hadavin_kit/docgen.py" not in RUNTIME and "hadavin_kit/testing.py" not in RUNTIME


@pytest.mark.skipif(
    bool(os.environ.get("HADAVIN_MUTATION_RUN")),
    reason="a mutated runtime file legitimately differs from the manifest; the mutation harness would count that as a kill",
)
def test_the_kits_own_sums_are_current_and_checks_out():
    """KIT-SHA256SUMS must be regenerated (python tools/vendor.py manifest) with every runtime change."""
    assert (KIT / "KIT-SHA256SUMS").read_text(encoding="utf-8") == kitsum.sums_text(
        kitsum.digests(KIT)
    )
    assert kitsum.check(KIT) == []


def test_copy_writes_a_lock_that_checks_out(tmp_path):
    dest = vend(tmp_path)
    lock = json.loads((dest.parent / "KIT.lock").read_text(encoding="utf-8"))
    assert lock["kit_commit"] == COMMIT and lock["format"] == kitsum.LOCK_FORMAT
    assert lock["vendor_dir"] == "vendor/portal-kit"
    assert sorted(lock["files"]) == RUNTIME and lock["tree_sha256"] == kitsum.tree_hash(
        lock["files"]
    )
    assert "hadavin-sdk" in lock["requires"] and "jsonschema" in lock["requires"]
    assert lock["schema_minor_max"] == 1
    assert kitsum.check(dest, dest.parent / "KIT.lock") == []
    assert not (dest / "hadavin_kit" / "docgen.py").exists() and not (dest / "tests").exists()
    cli = run_py(
        ["-m", "hadavin_kit.kitsum", "--check", str(dest.parent.parent)],
        cwd=tmp_path,
        pythonpath=dest,
    )
    assert cli.returncode == 0 and "OK" in cli.stdout


def test_a_changed_missing_or_extra_runtime_file_is_found(tmp_path):
    dest = vend(tmp_path)
    (dest / "hadavin_kit" / "chain.py").write_text("# edited\n", encoding="utf-8")
    (dest / "console" / "app.css").unlink()
    (dest / "hadavin_kit" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    got = " | ".join(kitsum.check(dest, dest.parent / "KIT.lock"))
    assert "hadavin_kit/chain.py: changed since it was vendored" in got
    assert "console/app.css: missing" in got
    assert "hadavin_kit/extra.py: not in the lock" in got
    cli = run_py(
        ["-m", "hadavin_kit.kitsum", "--check", str(dest.parent.parent)],
        cwd=tmp_path,
        pythonpath=KIT,
    )
    assert cli.returncode == 1 and "MISMATCH" in cli.stdout


def test_an_edited_lock_is_found_and_a_missing_lock_is_reported(tmp_path):
    dest = vend(tmp_path)
    lock = json.loads((dest.parent / "KIT.lock").read_text(encoding="utf-8"))
    lock["tree_sha256"] = "0" * 64
    (dest.parent / "KIT.lock").write_text(json.dumps(lock), encoding="utf-8")
    assert any("tree hash" in m for m in kitsum.check(dest, dest.parent / "KIT.lock"))
    (dest.parent / "KIT.lock").write_text("not json", encoding="utf-8")
    assert any("unreadable" in m for m in kitsum.check(dest, dest.parent / "KIT.lock"))
    (dest.parent / "KIT.lock").unlink()
    assert any("unreadable" in m for m in kitsum.check(dest, dest.parent / "KIT.lock"))


def test_line_endings_are_part_of_a_files_identity(tmp_path):
    dest = vend(tmp_path)
    f = dest / "console" / "app.js"
    f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))  # a CRLF checkout of the same content
    assert (
        kitsum.check(dest, dest.parent / "KIT.lock") != []
    )  # raw bytes, as the vendoring format says


@pytest.mark.parametrize("bad", ["abc1234", "G" * 40, "a" * 39, "a" * 41, ""])
def test_copy_needs_a_full_commit_id(tmp_path, bad):
    assert vendor.cmd_copy(tmp_path / "v", bad) == 2
    assert not (tmp_path / "v").exists()


def test_copy_refuses_a_commit_that_is_not_merged_into_main(tmp_path, monkeypatch):
    monkeypatch.delenv("HADAVIN_VENDOR_ALLOW_UNMERGED")
    if not (KIT / ".git").exists():
        pytest.skip("not a git checkout")
    assert vendor.cmd_copy(tmp_path / "v", "1" * 40) == 2


def test_a_vendored_copy_runs_kitcheck_without_the_original_tree(tmp_path):
    dest = vend(tmp_path)
    doc = example("refunds")
    doc["demo"]["kit_commit"] = COMMIT
    out = tmp_path / "export"
    out.mkdir()
    (out / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    page = run_py(
        [
            str(dest / "console" / "build_console.py"),
            str(out / "export.json"),
            "-o",
            str(out / "index.html"),
        ],
        cwd=tmp_path,
        pythonpath=dest,
    )
    assert page.returncode == 0, page.stderr
    res = run_py(
        [
            "-m",
            "hadavin_kit.kitcheck",
            str(out),
            "--demo",
            "refunds",
            "--kit-lock",
            str(dest.parent / "KIT.lock"),
        ],
        cwd=tmp_path,
        pythonpath=dest,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "OK" in res.stdout
    (dest / "console" / "app.css").write_text("/* tampered */", encoding="utf-8")
    bad = run_py(
        ["-m", "hadavin_kit.kitcheck", str(out), "--kit-lock", str(dest.parent / "KIT.lock")],
        cwd=tmp_path,
        pythonpath=dest,
    )
    assert bad.returncode == 1 and "changed since it was vendored" in bad.stdout


def test_a_copy_that_took_only_the_package_says_what_is_missing(tmp_path):
    only_pkg = tmp_path / "pkg_only"
    (only_pkg / "hadavin_kit").mkdir(parents=True)
    for p in (KIT / "hadavin_kit").glob("*.py"):
        (only_pkg / "hadavin_kit" / p.name).write_bytes(p.read_bytes())
    out = tmp_path / "export"
    out.mkdir()
    (out / "export.json").write_text(json.dumps(example("refunds")), encoding="utf-8")
    res = run_py(["-m", "hadavin_kit.kitcheck", str(out)], cwd=tmp_path, pythonpath=only_pkg)
    assert (
        res.returncode == 2
        and "vendored kit is incomplete" in res.stdout
        and "schema" in res.stdout
    )
    with pytest.raises(paths.KitLayoutError):
        paths.require(tmp_path / "nowhere")


# --------------------------------------------------------------------------- kitcheck --kit-lock / --deploy
def lock_file(tmp_path, commit=COMMIT, minor_max=1):
    p = tmp_path / "KIT.lock"
    data = kitsum.lock_data(
        KIT
    )  # built from the live files, so a mutated file does not fail every lock test
    p.write_text(
        json.dumps(
            {
                "format": kitsum.LOCK_FORMAT,
                "kit_commit": commit,
                "schema_minor_max": minor_max,
                **data,
            }
        ),
        encoding="utf-8",
    )
    return p


def export_dir(tmp_path, kit_commit):
    doc = example("askdata")
    doc["demo"]["kit_commit"] = kit_commit
    d = tmp_path / "exp"
    d.mkdir(exist_ok=True)
    (d / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


@pytest.mark.parametrize(
    ("claimed", "text"),
    [
        ("uncommitted", "not a full 40-hex"),
        ("abcdef1", "not a full 40-hex"),
        ("b" * 40, "differs from the kit_commit"),
    ],
)
def test_kit_commit_must_be_the_vendored_commit(tmp_path, claimed, text):
    res = kitcheck.run(export_dir(tmp_path, claimed), kit_lock=lock_file(tmp_path))
    assert any(p.code == "KIT_LOCK" and text in p.message for p in res["problems"]), [
        str(p) for p in res["problems"]
    ]


def test_matching_kit_commit_passes_and_absence_is_noted(tmp_path):
    d = export_dir(tmp_path, COMMIT)
    assert kitcheck.run(d, kit_lock=lock_file(tmp_path))["ok"]
    res = kitcheck.run(d)
    assert any("no --kit-lock" in n for n in res["notes"])


def test_export_newer_than_the_vendored_kit_is_refused(tmp_path):
    res = kitcheck.run(export_dir(tmp_path, COMMIT), kit_lock=lock_file(tmp_path, minor_max=0))
    assert any("newer than the vendored kit" in p.message for p in res["problems"])


def test_unreadable_lock_is_a_problem(tmp_path):
    bad = tmp_path / "KIT.lock"
    bad.write_text("{}", encoding="utf-8")
    res = kitcheck.run(export_dir(tmp_path, COMMIT), kit_lock=bad)
    assert any("unreadable" in p.message for p in res["problems"])


def test_deploy_mode_needs_the_head_and_the_lock(tmp_path):
    d = export_dir(tmp_path, COMMIT)
    res = kitcheck.run(d, deploy=True)
    msgs = " | ".join(p.message for p in res["problems"])
    assert "--deploy needs --expect-head" in msgs and "--deploy needs --kit-lock" in msgs
    head = example("askdata")["audit"]["events"][-1]["sha256"]
    ok = kitcheck.run(d, deploy=True, expect_head=head, kit_lock=lock_file(tmp_path))
    assert ok["ok"], [str(p) for p in ok["problems"]]


def test_testing_helpers_stay_out_of_the_vendored_set():
    assert testing.KIT == KIT
