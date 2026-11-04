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
from pathlib import Path

import pytest
from hadavin_kit import docgen, evalcore, kitcheck, kitsum, paths, strict_json
from hadavin_kit.testing import KIT

sys.path.insert(0, str(KIT / "console"))
sys.path.insert(0, str(KIT / "tools"))
import build_console

import check_fixture
import mutation
import vendor

CONSOLE = KIT / "console"
TINY = '{"tiny": true}'  # not a valid export: the tests only need a file that an unsafe path must not reach


def _no_links(why):
    if os.environ.get("CI") and os.name != "nt":
        pytest.fail(why + ": on the CI these tests must run, not skip")
    pytest.skip(why)


def make_link(link, target):
    """A directory link (symlink, or a junction on Windows); the test is skipped when neither can be made."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            _no_links("cannot create a symlink here")
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
        )  # fmt: skip
        if res.returncode != 0:
            _no_links("cannot create a symlink or junction here")


def make_file_link(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        if os.name == "nt":
            pytest.skip(
                "cannot create a file symlink here (Windows needs a privilege); they run on the Linux CI"
            )
        _no_links("cannot create a file symlink here")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """work/ is the current directory (the base); outside/ holds a valid export a bad path must not reach."""
    work, outside = tmp_path / "work", tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (outside / "export.json").write_text(TINY, encoding="utf-8", newline="\n")
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
        "...",
        "",
        "a\0b",
    ],
)
def test_unsafe_reason_refuses_every_traversal_form(bad):
    assert paths.unsafe_reason(bad)
    with pytest.raises(paths.UnsafePath):
        paths.safe_join("base", bad)


@pytest.mark.parametrize(
    "good", ["a", "a/b.json", "a b/c", ".hidden", "a..b/c", ".", "./a", "a/./b"]
)
def test_unsafe_reason_accepts_plain_relative_names(good):
    assert paths.unsafe_reason(good) is None


def test_safe_join_refuses_a_link_leaving_the_base(world):
    work, outside = world
    make_link(work / "ln", outside)
    with pytest.raises(paths.UnsafePath):
        paths.safe_join(work, "ln/export.json")
    assert paths.safe_join(work, "plain/export.json") == work / "plain" / "export.json"


def test_cli_path_confines_to_the_current_directory(world):
    work, outside = world
    (work / "d").mkdir()
    assert paths.cli_path("d") == work / "d"
    assert paths.cli_path(str(work / "d")) == work / "d"  # absolute but already inside the base
    for bad in ("../outside", str(outside), "C:\\x", "d/../../outside"):
        with pytest.raises(paths.UnsafePath):
            paths.cli_path(bad)


def test_read_in_refuses_a_file_link_leaving_the_folder(world):
    work, outside = world
    make_file_link(work / "r.json", outside / "export.json")
    with pytest.raises(paths.UnsafePath):
        paths.read_text_in(work, "r.json")
    with pytest.raises(ValueError, match="unsafe"):
        strict_json.load_object(work / "r.json")


# ---- command-line paths --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["dotdot", "absolute", "link"])
def test_kitcheck_cli_refuses_a_directory_outside_the_current_directory(world, capsys, how):
    work, outside = world
    if how == "link":
        make_link(work / "ln", outside)
    arg = {"dotdot": "../outside", "absolute": str(outside), "link": "ln"}[how]
    assert kitcheck.main([arg]) == 2
    assert "unsafe path" in capsys.readouterr().out


def test_kitcheck_cli_refuses_deny_list_and_kit_lock_outside(world, capsys):
    work, outside = world
    (outside / "terms.txt").write_text("secret\n", encoding="utf-8")
    (work / "export.json").write_text(TINY, encoding="utf-8")
    assert kitcheck.main([".", "--deny-list", "../outside/terms.txt"]) == 2
    assert kitcheck.main([".", "--kit-lock", str(outside / "KIT.lock")]) == 2
    assert capsys.readouterr().out.count("unsafe path") == 2


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


def test_build_console_cli_refuses_an_output_outside(world, capsys, monkeypatch):
    monkeypatch.setattr(
        build_console, "build", lambda doc: "<html></html>"
    )  # no schema needed here
    work, outside = world
    (work / "export.json").write_text(TINY, encoding="utf-8", newline="\n")
    assert build_console.main(["export.json", "-o", "../outside/index.html"]) == 2
    assert build_console.main(["export.json", "-o", str(outside / "index.html")]) == 2
    assert not (outside / "index.html").exists()
    assert build_console.main(["export.json"]) == 0 and (work / "index.html").is_file()


def test_build_console_refuses_a_headers_link_leaving_the_folder(world, capsys, monkeypatch):
    monkeypatch.setattr(build_console, "build", lambda doc: "<html></html>")
    work, outside = world
    (work / "export.json").write_text(TINY, encoding="utf-8", newline="\n")
    make_file_link(work / "_headers", outside / "stolen")
    assert build_console.main(["export.json"]) == 2
    assert not (outside / "stolen").exists()


def test_kitsum_and_vendor_cli_refuse_a_directory_outside(world, capsys):
    _, outside = world
    assert kitsum.main(["--check", "../outside"]) == 2
    assert kitsum.main(["--print", str(outside)]) == 2
    assert vendor.main(["verify", "../outside"]) == 2
    assert vendor.main(["copy", "../outside", "--commit", "0" * 40]) == 2


def test_check_fixture_refuses_a_slug_with_a_path(world, capsys):
    assert check_fixture.main(["../../x"]) == 2


@pytest.mark.skipif(
    bool(os.environ.get("HADAVIN_MUTATION_RUN")),
    reason="the mutation harness runs these tests inside a mutated copy: no recursion, and a mutated runtime file legitimately differs from the manifest",
)
def test_mutation_cli_refuses_targets_and_only_outside(world, capsys):
    _, outside = world
    (outside / "report.txt").write_text("", encoding="utf-8")
    assert mutation.main(["--only", "../outside/report.txt", "--targets", "chain.py"]) == 2
    assert mutation.main(["--targets", "../../outside/x.py"]) == 2


# ---- paths that come from a file's content -------------------------------------------------------------------------


def test_sums_file_naming_a_path_outside_is_a_problem_and_not_read(world):
    work, outside = world
    (outside / "secret.py").write_text("x", encoding="utf-8")
    (work / "KIT-SHA256SUMS").write_text(f"{'0' * 64}  ../outside/secret.py\n", encoding="utf-8")
    out = kitsum.check(work, work / "KIT-SHA256SUMS")
    assert out and all("not a safe relative path" in m for m in out)


@pytest.mark.parametrize("name", ["../outside/secret.py", "/abs/secret.py", "C:x"])
def test_kit_lock_naming_a_path_outside_is_a_problem(world, name):
    work, _ = world
    (work / "KIT.lock").write_text(
        json.dumps({"kit_commit": "a" * 40, "files": {name: "0" * 64}}), encoding="utf-8"
    )
    assert any("safe relative path" in m for m in kitsum.check(work, work / "KIT.lock"))


def test_a_planted_link_in_a_vendored_tree_is_reported_not_followed(world):
    work, outside = world
    (work / "hadavin_kit").mkdir()
    make_file_link(work / "hadavin_kit" / "evil.py", outside / "export.json")
    got = kitsum.digests(work)
    assert got == {"hadavin_kit/evil.py": kitsum._LEAVES_FOLDER}
    assert hashlib.sha256((outside / "export.json").read_bytes()).hexdigest() not in got.values()


def test_vendor_dir_that_leaves_the_demo_is_refused(world):
    work, _ = world
    for bad in ("../x", "/x", "C:x", "a\\b"):
        assert kitsum.vendor_dir_of(work, {"vendor_dir": bad})[0] is None


def test_eval_lock_naming_a_path_outside_is_reported_as_bad(world):
    work, outside = world
    (outside / "dev.jsonl").write_text("x", encoding="utf-8")
    lock = evalcore.Lock(files={"../outside/dev.jsonl": "0" * 64}, thresholds=evalcore.Thresholds())
    assert evalcore.verify_files(work, lock) == ["../outside/dev.jsonl"]
    with pytest.raises(paths.UnsafePath):
        evalcore.freeze(work, ["../outside/dev.jsonl"])
    assert not (work / "lock.json").exists()


def test_report_json_link_leaving_the_directory_is_not_read(tmp_path, world, monkeypatch):
    _, outside = world
    digest = hashlib.sha256(b"{}").hexdigest()
    doc = {
        "schema_minor": 1,
        "ledger": {"report_sha256": digest},
        "audit": {"events": [], "chain": "hash-chain/1"},
    }
    monkeypatch.setattr(
        kitcheck.validate, "load_and_validate", lambda path: (doc, [])
    )  # no sample export needed
    (outside / "report.json").write_text("{}", encoding="utf-8")
    d = tmp_path / "deploy"
    d.mkdir()
    (d / "export.json").write_text(TINY, encoding="utf-8", newline="\n")
    make_file_link(d / "report.json", outside / "report.json")
    res = kitcheck.run(d, require_report=True)
    codes = {p.code for p in res["problems"]}
    assert "REPORT_MISMATCH" not in codes  # the outside file was not hashed
    assert "MISSING_FILE" in codes


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


# ---- review round: default directory, messages, odd names ------------------------------------------------------------


@pytest.mark.skipif(
    bool(os.environ.get("HADAVIN_MUTATION_RUN")),
    reason="the mutation harness runs these tests inside a mutated copy: no recursion, and a mutated runtime file legitimately differs from the manifest",
)
def test_kitsum_default_directory_is_the_kit_root_from_any_current_directory(world, capsys):
    assert (
        kitsum.main(["--check"]) == 0
    )  # run from work/: the default (the kit root) is not confined to it
    assert "OK" in capsys.readouterr().out


def test_vendor_outside_the_current_directory_says_what_to_do(world, capsys):
    assert vendor.main(["copy", "../outside", "--commit", "0" * 40]) == 2
    out = capsys.readouterr().out
    assert (
        "must stay inside the current directory" in out
        and "cd <demo>; python ../portal-kit/tools/vendor.py copy ." in out
    )


@pytest.mark.skipif(os.name == "nt", reason="a colon cannot be part of a file name on Windows")
def test_a_file_name_with_a_colon_is_a_note_not_a_traceback(tmp_path):
    (tmp_path / "a:b.json").write_text("{}", encoding="utf-8")
    res = kitcheck.run(tmp_path, deny=["x"])  # must not raise
    assert any("not scanned" in n for n in res["notes"])
    assert any(
        p.code == "UNEXPECTED_FILE" for p in res["problems"]
    )  # and the folder is still not OK
    assert not res["ok"]


def test_absolute_cli_path_with_a_colon_in_the_file_name_is_refused(world):
    work, _ = world
    with pytest.raises(paths.UnsafePath, match="colon"):
        paths.cli_path(str(work / "file:stream"))


def test_build_console_missing_output_folder_is_exit_2(world, capsys, monkeypatch):
    monkeypatch.setattr(build_console, "build", lambda doc: "<html></html>")
    (world[0] / "export.json").write_text(TINY, encoding="utf-8")
    assert build_console.main(["export.json", "-o", "no/such/dir/index.html"]) == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["CON", "nul", "a/COM1.txt", "a. ", ".. ", "a /b", "LPT9.x/y"])
def test_windows_device_and_trailing_dot_or_space_names_are_refused_everywhere(bad):
    assert paths.unsafe_reason(bad)


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names exist only on Windows")
def test_windows_short_name_cannot_escape_the_base(tmp_path):
    base = tmp_path / "Program Files Long Name"
    base.mkdir()
    (base / "file.txt").write_text("x", encoding="utf-8")
    short = os.path.basename(
        subprocess.run(
            ["cmd", "/c", "for", "%I", "in", f'("{base}")', "do", "@echo", "%~sI"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    for name in (short, "PROGRA~1", "CON", "NUL"):
        try:
            got = paths.safe_join(tmp_path, name)
        except paths.UnsafePath:
            continue
        assert paths.inside(tmp_path, got)  # accepted names still resolve inside the base


@pytest.mark.skipif(
    bool(os.environ.get("HADAVIN_MUTATION_RUN")),
    reason="the mutation harness runs these tests inside a mutated copy: no recursion, and a mutated runtime file legitimately differs from the manifest",
)
def test_dot_means_the_current_directory_and_vendor_verify_dot_works(world, monkeypatch):
    work, _ = world
    assert paths.cli_path(".") == work
    monkeypatch.chdir(KIT)  # the CI step: `python tools/vendor.py verify .` from the kit root
    assert vendor.main(["verify", "."]) == 0


# ---- round 2 review: confine pitfalls, write through a directory link, vendor copy through a link -----------------------


def test_confine_sibling_prefix_root_base_and_the_base_itself(tmp_path):
    work, sibling = tmp_path / "w", tmp_path / "w2"  # "w2" starts with "w" but is not inside it
    work.mkdir()
    sibling.mkdir()
    with pytest.raises(paths.UnsafePath):
        paths.confine(work, sibling)
    assert paths.confine(work, work) == Path(os.path.realpath(work))
    root = Path(tmp_path.anchor)  # a base that is a drive or filesystem root
    assert paths.confine(root, tmp_path) == Path(os.path.realpath(tmp_path))
    assert paths.confine(work, work / "a" / "b.txt").name == "b.txt"
    assert not paths.inside(work, tmp_path)


def test_write_text_in_refuses_to_write_through_a_directory_link(world):
    work, outside = world
    make_link(work / "ln", outside)
    with pytest.raises(paths.UnsafePath):
        paths.write_text_in(work, "ln/x.txt", "data")
    assert not (outside / "x.txt").exists()
    paths.write_text_in(work, "ok.txt", "data")
    assert (work / "ok.txt").read_text(encoding="utf-8") == "data"


def _copy_env(monkeypatch):
    monkeypatch.setenv(vendor.ALLOW_ENV, "1")  # tests only: skip the merged-commit check


def test_vendor_copy_refuses_a_vendor_directory_that_is_a_link_and_touches_nothing_outside(
    world, monkeypatch, capsys
):
    work, outside = world
    _copy_env(monkeypatch)
    (outside / "portal-kit").mkdir()
    keep = outside / "portal-kit" / "precious.txt"
    keep.write_text("keep", encoding="utf-8")
    demo = work / "demo"
    demo.mkdir()
    make_link(demo / "vendor", outside)  # a symlink, or a junction on Windows
    assert vendor.cmd_copy(demo, "a" * 40) == 2
    assert "must not be links" in capsys.readouterr().out
    assert keep.read_text(encoding="utf-8") == "keep"  # not deleted by the wipe
    assert not (outside / "KIT.lock").exists()


def test_vendor_copy_refuses_a_portal_kit_link_and_a_kit_lock_symlink(world, monkeypatch):
    work, outside = world
    _copy_env(monkeypatch)
    demo = work / "demo"
    (demo / "vendor").mkdir(parents=True)
    make_link(demo / "vendor" / "portal-kit", outside)
    assert vendor.cmd_copy(demo, "a" * 40) == 2
    assert (outside / "export.json").is_file()
    demo2 = work / "demo2"
    (demo2 / "vendor").mkdir(parents=True)
    make_file_link(demo2 / "vendor" / "KIT.lock", outside / "lock-target")
    assert vendor.cmd_copy(demo2, "a" * 40) == 2
    assert not (outside / "lock-target").exists()


@pytest.mark.parametrize("not_text", [None, 5, b"a", ["a"]])
def test_unsafe_reason_refuses_a_value_that_is_not_text(not_text):
    assert paths.unsafe_reason(not_text)


# ---- mutation survivors of the first run: tests that pin the vendor command and the in-demo link check --------------------


def test_vendor_copy_refuses_links_that_point_inside_the_demo_too(world, monkeypatch):
    work, _ = world
    _copy_env(monkeypatch)
    demo = work / "demo"
    (demo / "elsewhere").mkdir(parents=True)
    (demo / "vendor").mkdir()
    make_link(
        demo / "vendor" / "portal-kit", demo / "elsewhere"
    )  # inside the demo, but not where the lock says
    assert vendor.cmd_copy(demo, "a" * 40) == 2
    demo2 = work / "demo2"
    (demo2 / "real").mkdir(parents=True)
    make_link(demo2 / "vendor", demo2 / "real")
    assert vendor.cmd_copy(demo2, "a" * 40) == 2
    demo3 = work / "demo3"
    (demo3 / "vendor").mkdir(parents=True)
    (demo3 / "other.lock").write_text("x", encoding="utf-8")
    make_file_link(demo3 / "vendor" / "KIT.lock", demo3 / "other.lock")  # only the lock is a link
    assert vendor.cmd_copy(demo3, "a" * 40) == 2


def test_vendor_main_copy_and_manifest_commands_work(world, monkeypatch, capsys):
    work, _ = world
    _copy_env(monkeypatch)
    assert vendor.main(["copy", "demo", "--commit", "a" * 40]) == 0
    assert (work / "demo" / "vendor" / "KIT.lock").is_file()
    assert vendor.main(["verify", "demo"]) == 0


def test_vendor_manifest_command_writes_the_manifest_of_the_kit_it_runs_in(tmp_path, monkeypatch):
    # Run on a TEMPORARY copy of the runtime files: the repository's own KIT-SHA256SUMS must not be rewritten by a test
    # (that would hide a stale committed manifest; tests/test_vendor.py is the currency check).
    tmp_kit = tmp_path / "kit"
    for folder in ("hadavin_kit", "schema", "console"):
        shutil.copytree(
            KIT / folder, tmp_kit / folder, ignore=shutil.ignore_patterns("__pycache__")
        )
    real = (KIT / "KIT-SHA256SUMS").read_bytes()
    monkeypatch.setattr(vendor, "KIT", tmp_kit)
    (tmp_kit / "console" / "chain.js").write_text("// changed on purpose\n", encoding="utf-8")
    assert vendor.main(["manifest"]) == 0
    written = (tmp_kit / "KIT-SHA256SUMS").read_text(encoding="utf-8")
    assert written == kitsum.sums_text(kitsum.digests(tmp_kit))
    assert written.encode() != real  # it describes the changed copy, not the repository
    assert (
        KIT / "KIT-SHA256SUMS"
    ).read_bytes() == real  # and the repository's file was not touched


def test_mutation_target_is_validated_before_the_package_prefix_is_added():
    for bad in ("/chain.py", r"\chain.py", "C:chain.py", "../chain.py"):
        with pytest.raises(paths.UnsafePath):
            mutation.src_path(bad)
    assert mutation.src_path("chain.py").name == "chain.py"


def test_sampling_can_pick_either_of_two_sites_that_share_one_key():
    a = mutation.Mutant("m.py", 1, "cmp Is->IsNot", 10, "check", "bound is None")
    b = mutation.Mutant("m.py", 2, "cmp Is->IsNot", 20, "check", "bound is None")
    assert a.key == b.key
    picked = {mutation.sample([a, b], seed, 1)[0].index for seed in range(40)}
    assert picked == {1, 2}  # keyed by text only, one site could never be chosen
    assert mutation.sample([a, b], 7, 1) == mutation.sample(
        [b, a], 7, 1
    )  # reproducible, order-independent


@pytest.mark.parametrize("head", [None, "0" * 40 + chr(10)])
def test_source_problem_refuses_a_missing_or_different_head(tmp_path, monkeypatch, head):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(vendor, "KIT", tmp_path)
    monkeypatch.setattr(vendor, "_git", lambda *args: head)
    assert "HEAD is not --commit" in vendor.source_problem("a" * 40)


def test_kitsum_ignores_only_the_known_junk_names_and_reports_bad_hashes():
    assert kitsum._ignorable(Path(".DS_Store")) and kitsum._ignorable(Path("Thumbs.db"))
    assert not kitsum._ignorable(Path("evil.py"))
    got = kitsum.lock_shape_problems({"files": {"a.py": "G" * 64}})
    assert got == ["a.py: the lock's hash is not 64 lowercase hex digits"]


def test_an_unlisted_file_in_each_of_the_three_kit_folders_is_found(tmp_path):
    for folder, name in (("hadavin_kit", "a.py"), ("schema", "b.json"), ("console", "c.js")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).write_text("x", encoding="utf-8")
    lock = tmp_path / "KIT.lock"
    lock.write_text(
        json.dumps({"kit_commit": "a" * 40, "files": {}, "tree_sha256": kitsum.tree_hash({})}),
        encoding="utf-8",
    )
    got = kitsum.check(tmp_path, lock)
    for rel in ("hadavin_kit/a.py", "schema/b.json", "console/c.js"):
        assert f"{rel}: not in the lock (unlisted file)" in got


def test_fold_removes_control_characters_and_format_characters_separately():
    from hadavin_kit import textnorm

    assert textnorm.fold("a" + chr(0) + "b") == "ab"  # a control character (category Cc)
    assert textnorm.fold("a" + chr(0x7F) + "b") == "ab"
    assert textnorm.fold("a" + chr(0x200B) + "b") == "ab"  # a format character (category Cf)
    assert textnorm.fold("a  b") == "a b"  # whitespace collapses to one space, which is not removed
