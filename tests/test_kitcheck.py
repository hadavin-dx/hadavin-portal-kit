"""kitcheck: the conformance kit a demo runs on its export directory. Each check has a pass and a failing case."""

import json
import os
import subprocess
import sys

import pytest
from hadavin_kit import chain, kitcheck, testing
from hadavin_kit.testing import KIT, SLUGS, example

sys.path.insert(0, str(KIT / "console"))
import build_console


def write_dir(root, doc, *, page=True, headers=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "export.json").write_text(json.dumps(doc, indent=2), encoding="utf-8", newline="\n")
    if page:
        (root / "index.html").write_text(build_console.build(doc), encoding="utf-8", newline="\n")
    if headers:
        _, css, js = build_console.assets()
        (root / "_headers").write_text(
            build_console.headers_file(build_console.csp_for(js, css)),
            encoding="utf-8",
            newline="\n",
        )
    return root


def codes(res):
    return {p.code for p in res["problems"]}


@pytest.mark.parametrize("slug", SLUGS)
def test_every_example_directory_conforms(tmp_path, slug):
    res = kitcheck.run(write_dir(tmp_path / slug, example(slug)), demo=slug)
    assert res["ok"] and res["problems"] == [] and res["minor"] == 1
    assert res["chain"]["ok"] and len(res["chain"]["head"]) == 64
    assert "fixture" in res["honest"] and "scripted" in res["honest"]
    assert any("deny-list" in n for n in res["notes"])  # said, not silently skipped


def test_minor_zero_directory_also_conforms(tmp_path):
    res = kitcheck.run(write_dir(tmp_path / "old", testing.compat("refunds")))
    assert res["ok"] and res["minor"] == 0


def test_export_only_directory_is_fine_and_says_the_page_was_not_checked(tmp_path):
    res = kitcheck.run(write_dir(tmp_path / "e", example("refunds"), page=False, headers=False))
    assert res["ok"] and any("console page not checked" in n for n in res["notes"])


def test_tampered_chain_and_wrong_demo(tmp_path):
    d = example("refunds")
    d["audit"]["events"][3]["summary"] += " tampered"
    res = kitcheck.run(write_dir(tmp_path / "t", d, page=False, headers=False), demo="intake")
    assert {"CHAIN", "RULE"} <= codes(res) and not res["ok"]


def test_invalid_export_reports_typed_problems_and_no_chain_head(tmp_path):
    d = example("refunds")
    d["surprise"] = 1
    res = kitcheck.run(write_dir(tmp_path / "u", d, page=False, headers=False))
    assert "UNKNOWN_KEY" in codes(res) and res["chain"] is None


def test_missing_export_and_not_a_directory(tmp_path):
    (tmp_path / "empty").mkdir()
    assert "MISSING_FILE" in codes(kitcheck.run(tmp_path / "empty"))
    res = kitcheck.run(tmp_path / "nope")
    assert res["unreadable"] and "MISSING_FILE" in codes(res)


@pytest.mark.parametrize(
    "name",
    [
        "model.safetensors",
        "index.faiss",
        "notes.ipynb",
        "dump.sqlite",
        "weights.pkl",
        "extra.txt",
        "x.zip",
    ],
)
def test_hygiene_rejects_unexpected_and_forbidden_files(tmp_path, name):
    root = write_dir(tmp_path / "h", example("refunds"))
    (root / name).write_bytes(b"x")
    res = kitcheck.run(root)
    assert "UNEXPECTED_FILE" in codes(res)
    msg = next(p.message for p in res["problems"] if p.path == name)
    assert ("forbidden file type" in msg) == name.endswith(
        (".safetensors", ".faiss", ".ipynb", ".sqlite", ".pkl", ".zip")
    )


def test_hygiene_rejects_subdirectories_and_oversize(tmp_path, monkeypatch):
    root = write_dir(tmp_path / "s", example("refunds"))
    (root / "sub").mkdir()
    (root / "sub" / "export.json").write_text("{}", encoding="utf-8")
    assert "UNEXPECTED_FILE" in codes(kitcheck.run(root))
    root2 = write_dir(tmp_path / "o", example("refunds"), page=False, headers=False)
    monkeypatch.setattr(kitcheck, "MAX_FILE_BYTES", 100)
    assert "UNEXPECTED_FILE" in codes(kitcheck.run(root2))


def test_symlink_is_rejected(tmp_path):
    root = write_dir(tmp_path / "l", example("refunds"), page=False, headers=False)
    try:
        os.symlink(root / "export.json", root / "report.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert "UNEXPECTED_FILE" in codes(kitcheck.run(root))


def test_page_whose_data_differs_from_export_is_rejected(tmp_path):
    root = write_dir(tmp_path / "p", example("refunds"))
    other = example("intake")
    (root / "index.html").write_text(build_console.build(other), encoding="utf-8")
    res = kitcheck.run(root)
    assert "PAGE_MISMATCH" in codes(res) and any(
        "differs from export.json" in p.message for p in res["problems"]
    )


def test_edited_script_breaks_the_csp_hash(tmp_path):
    root = write_dir(tmp_path / "j", example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(
        page.replace("'use strict';", "'use strict'; /* edited */", 1), encoding="utf-8"
    )
    assert any("hash is not in the page CSP" in p.message for p in kitcheck.run(root)["problems"])


def test_headers_file_must_match_and_exist(tmp_path):
    root = write_dir(tmp_path / "x", example("refunds"))
    (root / "_headers").write_text(
        "/*\n  Content-Security-Policy: default-src *\n", encoding="utf-8"
    )
    assert any(p.path == "_headers" for p in kitcheck.run(root)["problems"])
    (root / "_headers").unlink()
    assert "MISSING_FILE" in codes(kitcheck.run(root))


def test_remote_reference_in_page_is_rejected(tmp_path):
    root = write_dir(tmp_path / "r", example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(
        page.replace(
            "<body>", '<body><link rel="stylesheet" href="https://cdn.example.org/a.css">', 1
        ),
        encoding="utf-8",
    )
    assert "PAGE_MISMATCH" in codes(kitcheck.run(root))


def test_non_console_page_is_rejected(tmp_path):
    root = write_dir(tmp_path / "n", example("refunds"))
    (root / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    assert any("not a console page" in p.message for p in kitcheck.run(root)["problems"])


def test_unsafe_csp_is_rejected(tmp_path):
    root = write_dir(tmp_path / "c", example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(
        page.replace("default-src 'none'", "default-src 'none'; script-src 'unsafe-inline'", 1),
        encoding="utf-8",
    )
    assert any("unsafe-inline" in p.message for p in kitcheck.run(root)["problems"])


def test_deny_list_catches_names_including_look_alikes(tmp_path):
    root = write_dir(tmp_path / "d", example("refunds"), page=False, headers=False)
    clean = kitcheck.run(root, deny=["Contoso Holdings", "canary-7f3a"])
    assert clean["ok"] and not any("deny-list" in n for n in clean["notes"])
    doc = example("refunds")
    doc["cases"][0]["input_summary"] = "Refund for ACME Corp order"
    root2 = write_dir(tmp_path / "d2", doc, page=False, headers=False)
    assert "DENYLIST_HIT" in codes(
        kitcheck.run(root2, deny=["\uff21\uff23\uff2d\uff25 corp"])
    )  # fullwidth ACME


def test_report_hash_must_match_when_the_report_is_present(tmp_path):
    doc = example("refunds")
    report = b'{"command": "x", "received": 1}'
    import hashlib

    digest = hashlib.sha256(report).hexdigest()
    doc["ledger"].update(scope="load_run", report_sha256=digest)
    doc["ledger"].update(received=len(doc["cases"]))
    root = write_dir(tmp_path / "rp", doc, page=False, headers=False)
    assert any("not verified" in n for n in kitcheck.run(root)["notes"])
    assert "MISSING_FILE" in codes(kitcheck.run(root, require_report=True))
    (root / "report.json").write_bytes(report)
    assert kitcheck.run(root)["ok"]
    (root / "report.json").write_bytes(report + b" ")
    assert "REPORT_MISMATCH" in codes(kitcheck.run(root))


def test_cli_exit_codes_and_json(tmp_path):
    good = write_dir(tmp_path / "good", example("askdata"))
    env = {**os.environ, "PYTHONPATH": str(KIT)}

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "hadavin_kit.kitcheck", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )

    ok = cli(str(good), "--demo", "askdata", "--json")
    assert ok.returncode == 0 and json.loads(ok.stdout)["ok"] is True
    (good / "stray.bin").write_bytes(b"1")
    bad = cli(str(good))
    assert bad.returncode == 1 and "UNEXPECTED_FILE" in bad.stdout
    assert cli(str(tmp_path / "absent")).returncode == 2


def test_chain_head_reported_equals_recomputed(tmp_path):
    d = example("dealdesk")
    res = kitcheck.run(write_dir(tmp_path / "hd", d, page=False, headers=False))
    assert res["chain"]["head"] == d["audit"]["events"][-1]["sha256"]
    assert chain.verify(d["audit"]["events"], d["audit"]["chain"])["ok"]
