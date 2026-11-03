"""Path safety (SonarCloud path-traversal class), the 32-bit wrap in chain.js, and the linear-time regex rewrites.

Every path that arrives from the command line or from a file's content goes through `hadavin_kit.paths`
(`cli_path`, `safe_join`, `read_*_in`): relative, no `..`, no drive, never leaving its base through a symlink or
junction. Each test below builds a target that EXISTS outside the base, so the old code would have read it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest
from hadavin_kit import docgen, evalcore, kitcheck, kitsum, paths, strict_json
from hadavin_kit.testing import KIT, example

sys.path.insert(0, str(KIT / "console"))
sys.path.insert(0, str(KIT / "tools"))
import build_console

import check_fixture
import mutation
import vendor

CONSOLE = KIT / "console"


def make_link(link, target):
    """A directory link (symlink, or a junction on Windows); the test is skipped when neither can be made."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("cannot create a symlink here")
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
        )  # fmt: skip
        if res.returncode != 0:
            pytest.skip("cannot create a symlink or junction here")


def make_file_link(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a file symlink here")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """work/ is the current directory (the base); outside/ holds a valid export a bad path must not reach."""
    work, outside = tmp_path / "work", tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (outside / "export.json").write_text(
        json.dumps(example("intake")), encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(work)
    return work, outside


# ---- the shared helper ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../x",
        "a/../x",
        "a\\..\\x",
        "..",
        "/etc/passwd",
        "\\x",
        "C:\\x",
        "C:x",
        "\\\\host\\share\\x",
        "a:b",
        "",
        "a\0b",
    ],
)
def test_unsafe_reason_refuses_every_traversal_form(bad):
    assert paths.unsafe_reason(bad)
    with pytest.raises(paths.UnsafePath):
        paths.safe_join("base", bad)


@pytest.mark.parametrize("good", ["a", "a/b.json", "a b/c", "...", "a..b/c"])
def test_unsafe_reason_accepts_plain_relative_names(good):
    assert paths.unsafe_reason(good) is None


@pytest.mark.needs_samples
def test_safe_join_refuses_a_link_leaving_the_base(world):
    work, outside = world
    make_link(work / "ln", outside)
    with pytest.raises(paths.UnsafePath):
        paths.safe_join(work, "ln/export.json")
    assert paths.safe_join(work, "plain/export.json") == work / "plain" / "export.json"


@pytest.mark.needs_samples
def test_cli_path_confines_to_the_current_directory(world):
    work, outside = world
    (work / "d").mkdir()
    assert paths.cli_path("d") == work / "d"
    assert paths.cli_path(str(work / "d")) == work / "d"  # absolute but already inside the base
    for bad in ("../outside", str(outside), "C:\\x", "d/../../outside"):
        with pytest.raises(paths.UnsafePath):
            paths.cli_path(bad)


@pytest.mark.needs_samples
def test_read_in_refuses_a_file_link_leaving_the_folder(world):
    work, outside = world
    make_file_link(work / "r.json", outside / "export.json")
    with pytest.raises(paths.UnsafePath):
        paths.read_text_in(work, "r.json")
    with pytest.raises(ValueError, match="unsafe"):
        strict_json.load_object(work / "r.json")


# ---- command-line paths --------------------------------------------------------------------------------------------


@pytest.mark.needs_samples
@pytest.mark.parametrize("how", ["dotdot", "absolute", "link"])
def test_kitcheck_cli_refuses_a_directory_outside_the_current_directory(world, capsys, how):
    work, outside = world
    if how == "link":
        make_link(work / "ln", outside)
    arg = {"dotdot": "../outside", "absolute": str(outside), "link": "ln"}[how]
    assert kitcheck.main([arg]) == 2
    assert "unsafe path" in capsys.readouterr().out


@pytest.mark.needs_samples
def test_kitcheck_cli_refuses_deny_list_and_kit_lock_outside(world, capsys):
    work, outside = world
    (outside / "terms.txt").write_text("secret\n", encoding="utf-8")
    (work / "export.json").write_text(json.dumps(example("intake")), encoding="utf-8")
    assert kitcheck.main([".", "--deny-list", "../outside/terms.txt"]) == 2
    assert kitcheck.main([".", "--kit-lock", str(outside / "KIT.lock")]) == 2
    assert capsys.readouterr().out.count("unsafe path") == 2


@pytest.mark.needs_samples
@pytest.mark.parametrize("how", ["dotdot", "absolute", "link"])
def test_build_console_cli_refuses_an_export_outside_and_writes_nothing(world, capsys, how):
    work, outside = world
    if how == "link":
        make_link(work / "ln", outside)
    arg = {
        "dotdot": "../outside/export.json",
        "absolute": str(outside / "export.json"),
        "link": "ln/export.json",
    }[how]
    assert build_console.main([arg]) == 2
    assert "unsafe path" in capsys.readouterr().err
    assert not (outside / "index.html").exists() and not (outside / "_headers").exists()


@pytest.mark.needs_samples
def test_build_console_cli_refuses_an_output_outside(world, capsys):
    work, outside = world
    (work / "export.json").write_text(json.dumps(example("intake")), encoding="utf-8", newline="\n")
    assert build_console.main(["export.json", "-o", "../outside/index.html"]) == 2
    assert build_console.main(["export.json", "-o", str(outside / "index.html")]) == 2
    assert not (outside / "index.html").exists()
    assert build_console.main(["export.json"]) == 0 and (work / "index.html").is_file()


@pytest.mark.needs_samples
def test_build_console_refuses_a_headers_link_leaving_the_folder(world, capsys):
    work, outside = world
    (work / "export.json").write_text(json.dumps(example("intake")), encoding="utf-8", newline="\n")
    make_file_link(work / "_headers", outside / "stolen")
    assert build_console.main(["export.json"]) == 2
    assert not (outside / "stolen").exists()


@pytest.mark.needs_samples
def test_kitsum_and_vendor_cli_refuse_a_directory_outside(world, capsys):
    _, outside = world
    assert kitsum.main(["--check", "../outside"]) == 2
    assert kitsum.main(["--print", str(outside)]) == 2
    assert vendor.main(["verify", "../outside"]) == 2
    assert vendor.main(["copy", "../outside", "--commit", "0" * 40]) == 2


@pytest.mark.needs_samples
def test_check_fixture_refuses_a_slug_with_a_path(world, capsys):
    assert check_fixture.main(["../../x"]) == 2


@pytest.mark.needs_samples
def test_mutation_cli_refuses_targets_and_only_outside(world, capsys):
    _, outside = world
    (outside / "report.txt").write_text("", encoding="utf-8")
    assert mutation.main(["--only", "../outside/report.txt", "--targets", "chain.py"]) == 2
    assert mutation.main(["--targets", "../../outside/x.py"]) == 2


# ---- paths that come from a file's content -------------------------------------------------------------------------


@pytest.mark.needs_samples
def test_sums_file_naming_a_path_outside_is_a_problem_and_not_read(world):
    work, outside = world
    (outside / "secret.py").write_text("x", encoding="utf-8")
    (work / "KIT-SHA256SUMS").write_text(f"{'0' * 64}  ../outside/secret.py\n", encoding="utf-8")
    out = kitsum.check(work, work / "KIT-SHA256SUMS")
    assert out and all("not a safe relative path" in m for m in out)


@pytest.mark.needs_samples
@pytest.mark.parametrize("name", ["../outside/secret.py", "/abs/secret.py", "C:x"])
def test_kit_lock_naming_a_path_outside_is_a_problem(world, name):
    work, _ = world
    (work / "KIT.lock").write_text(
        json.dumps({"kit_commit": "a" * 40, "files": {name: "0" * 64}}), encoding="utf-8"
    )
    assert any("safe relative path" in m for m in kitsum.check(work, work / "KIT.lock"))


@pytest.mark.needs_samples
def test_a_planted_link_in_a_vendored_tree_is_reported_not_followed(world):
    work, outside = world
    (work / "hadavin_kit").mkdir()
    make_file_link(work / "hadavin_kit" / "evil.py", outside / "export.json")
    got = kitsum.digests(work)
    assert got == {"hadavin_kit/evil.py": kitsum._LEAVES_FOLDER}
    assert hashlib.sha256((outside / "export.json").read_bytes()).hexdigest() not in got.values()


@pytest.mark.needs_samples
def test_vendor_dir_that_leaves_the_demo_is_refused(world):
    work, _ = world
    for bad in ("../x", "/x", "C:x", "a\\b"):
        assert kitsum.vendor_dir_of(work, {"vendor_dir": bad})[0] is None


@pytest.mark.needs_samples
def test_eval_lock_naming_a_path_outside_is_reported_as_bad(world):
    work, outside = world
    (outside / "dev.jsonl").write_text("x", encoding="utf-8")
    lock = evalcore.Lock(files={"../outside/dev.jsonl": "0" * 64}, thresholds=evalcore.Thresholds())
    assert evalcore.verify_files(work, lock) == ["../outside/dev.jsonl"]
    with pytest.raises(paths.UnsafePath):
        evalcore.freeze(work, ["../outside/dev.jsonl"])
    assert not (work / "lock.json").exists()


@pytest.mark.needs_samples
def test_report_json_link_leaving_the_directory_is_not_read(tmp_path, world):
    _, outside = world
    doc = example("intake")
    (outside / "report.json").write_text("{}", encoding="utf-8")
    doc["ledger"]["report_sha256"] = hashlib.sha256(b"{}").hexdigest()
    d = tmp_path / "deploy"
    d.mkdir()
    (d / "export.json").write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    make_file_link(d / "report.json", outside / "report.json")
    res = kitcheck.run(d, require_report=True)
    assert "REPORT_MISMATCH" not in {
        p.code for p in res["problems"]
    }  # the outside file was not hashed
    assert "MISSING_FILE" in {p.code for p in res["problems"]}


@pytest.mark.needs_samples
def test_recorded_turns_set_refuses_a_link_leaving_the_folder(world):
    from hadavin_kit import scripted_llm

    work, outside = world
    (outside / "t.json").write_text("{}", encoding="utf-8")
    (work / "rec").mkdir()
    make_file_link(work / "rec" / "t.json", outside / "t.json")
    with pytest.raises(paths.UnsafePath):
        scripted_llm.recorded_turns_sha256(work / "rec")


# ---- console/chain.js: `>>> 0` is the 32-bit wrap, not a truncation --------------------------------------------------

NODE_SHA = """
const m = require(process.argv[1]); const crypto = require('crypto');
const out = [];
const inputs = [''];
for (let n = 1; n <= 130; n++) inputs.push('a'.repeat(n));
inputs.push('x'.repeat(100000), '\\u00eb\\ud83d\\ude42'.repeat(500));
for (const s of inputs) out.push([m.sha256(s), crypto.createHash('sha256').update(s, 'utf8').digest('hex')]);
console.log(JSON.stringify(out));
"""


def _node_pairs(chain_js):
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI_REQUIRE_BROWSER"):
            pytest.fail("node is required")
        pytest.skip("node not installed")
    res = subprocess.run(
        [node, "-e", NODE_SHA, str(chain_js)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(res.stdout)


def test_chain_js_sha256_matches_the_standard_for_131_lengths_and_large_inputs(tmp_path):
    got = _node_pairs(CONSOLE / "chain.js")
    assert len(got) == 133 and all(a == b for a, b in got)


def test_chain_js_has_no_bitwise_or_zero_and_keeps_the_wrap():
    src = (CONSOLE / "chain.js").read_text(encoding="utf-8")
    assert "| 0" not in src
    assert src.count(") >>> 0") == 13  # w[t], T1, T2, e, a and the eight H sums


def test_chain_js_old_and_new_wrap_agree_but_dropping_the_wrap_breaks_the_hash(tmp_path):
    src = (CONSOLE / "chain.js").read_text(encoding="utf-8")
    old = tmp_path / "old.js"
    old.write_text(src.replace(") >>> 0", ") | 0"), encoding="utf-8")
    assert _node_pairs(old) == _node_pairs(
        CONSOLE / "chain.js"
    )  # same behaviour as before the change
    nowrap = tmp_path / "nowrap.js"
    nowrap.write_text(src.replace(") >>> 0", ")"), encoding="utf-8")
    assert any(
        a != b for a, b in _node_pairs(nowrap)
    )  # the test would notice a lost wrap (e.g. Math.trunc alone)


# ---- tools/mutation.py: linear-time regexes, no random generator --------------------------------------------------

OLD_GROUP = re.compile(r"\((?:\?[:=!])?([^()]*\|[^()]*)\)")
OLD_SURVIVOR = re.compile(r"\s*SURVIVOR (\S+?) \[(.+?)\] \((.*?)\) (.*)$")


@pytest.mark.parametrize(
    "text",
    [
        "(a|b)",
        "x(?:a|b|c)y",
        "(?=a|b)",
        "(a)",
        "((a|b)|c)",
        "(|)",
        "(a||b)",
        "a|b",
        "(?:a|b)(c|d)",
        "(\\|)",
    ],
)
def test_group_regex_finds_the_same_groups_as_before(text):
    assert [m.span(1) for m in mutation._GROUP.finditer(text)] == [
        m.span(1) for m in OLD_GROUP.finditer(text)
    ]


def test_group_regex_is_linear_on_a_long_adversarial_input():
    text = "(" + "a|" * 25000  # no closing bracket: the old pattern re-scans the run for every `|`
    t0 = time.perf_counter()
    assert list(mutation._GROUP.finditer(text)) == []
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.parametrize(
    "line",
    [
        "SURVIVOR kitcheck.py [cmp Eq->NotEq] (run) 12:4 x == y",
        "  SURVIVOR a/b.py [regex group0 drop[1]] () text",
        "SURVIVOR f [k] (fn) ",
        "SURVIVOR f [k] (fn)",
        "SURVIVOR  [k] (fn) t",
        "SURVIVOR f k (fn) t",
        "NOTHING",
        "SURVIVOR a b [k] (f) t",
    ],
)
def test_survivor_parser_agrees_with_the_old_regex(line):
    m = OLD_SURVIVOR.match(line)
    want = f"{m.group(1)}::{m.group(3)}::{m.group(2)}::{m.group(4)}" if m else None
    assert mutation.parse_survivor(line) == want


def test_survivor_parser_is_linear_on_a_long_adversarial_line():
    line = "SURVIVOR f " + "[x] (y" * 30000
    t0 = time.perf_counter()
    assert mutation.parse_survivor(line) is None
    assert mutation.parse_survivor("SURVIVOR f [" + "] (" * 30000) is None
    assert time.perf_counter() - t0 < 1.0


def test_mutation_sampling_uses_no_random_generator():
    src = (KIT / "tools" / "mutation.py").read_text(encoding="utf-8")
    assert (
        not re.search(r"^\s*(import random|from random)", src, re.MULTILINE)
        and "random." not in src
    )


# ---- docgen.main ----------------------------------------------------------------------------------------------------


def test_docgen_main_returns_nothing_and_leaves_the_committed_block_unchanged(monkeypatch):
    doc = KIT / "CONTRACT.md"
    before = doc.read_bytes()
    monkeypatch.setattr(sys, "argv", ["docgen"])
    assert (
        docgen.main() is None
    )  # failures are exceptions (non-zero exit); there is no status to return
    assert doc.read_bytes() == before
