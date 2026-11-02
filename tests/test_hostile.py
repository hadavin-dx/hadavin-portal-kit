"""Hostile and reviewed-gap tests (review of PR 5 at 476b853). Each one fails on that commit.

Non-ASCII text is built with chr() so no look-alike or invisible character sits in this source file.
"""

import copy
import json
import os
import subprocess
import sys

import pytest
from hadavin_kit import export_rules, kitcheck, validate
from hadavin_kit.testing import KIT, SLUGS, example

sys.path.insert(0, str(KIT / "console"))
import build_console

CYR_A = chr(0x430)  # Cyrillic small a, looks like Latin a
FULLWIDTH_A = chr(0xFF41)
U_UML = chr(0xFC)  # u with diaeresis


def write_dir(root, doc, *, page=True, ensure_ascii=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "export.json").write_text(json.dumps(doc, ensure_ascii=ensure_ascii), encoding="utf-8")
    if page:
        (root / "index.html").write_text(build_console.build(doc), encoding="utf-8")
        _, css, js = build_console.assets()
        (root / "_headers").write_text(
            build_console.headers_file(build_console.csp_for(js, css)), encoding="utf-8"
        )
    return root


def codes(doc):
    return {p.code for p in validate.validate(doc)}


# --------------------------------------------------------------------------- M1: look-alike keys and synonyms
@pytest.mark.parametrize("slug", SLUGS)
def test_non_ascii_key_in_free_form_objects_is_refused_in_every_demo(slug):
    d = example(slug)
    target = (
        d["approvals"][0]["args"] if d["approvals"] else d["cases"][0]["steps"][0]["args_summary"]
    )
    target[CYR_A + "mount_minor"] = "100"
    assert "CONFUSABLE_ID" in codes(d), slug
    d2 = example(slug)
    (d2["approvals"][0]["args"] if d2["approvals"] else d2["cases"][0]["steps"][0]["args_summary"])[
        FULLWIDTH_A + "mount"
    ] = 1
    assert "CONFUSABLE_ID" in codes(d2), slug


def test_a_look_alike_money_key_cannot_hide_a_money_action_from_the_tier_rules():
    d = example("apexc")
    a = next(x for x in d["approvals"] if x["tier"] == "human_nonmoney")
    a["args"] = {CYR_A + "mount": "100"}  # would have normalised to "mount": not money
    got = validate.validate(d)
    assert any(p.code == "CONFUSABLE_ID" for p in got)


@pytest.mark.parametrize("word", ["sum", "fee", "charge", "payout", "refund", "payment"])
def test_money_synonyms_count_as_money_keys(word):
    assert export_rules._has_money_key({"order": 1, f"customer_{word}": "5"}) is True
    d = example("apexc")
    a = next(x for x in d["approvals"] if x["tier"] == "human_nonmoney")
    a["args"] = {**a["args"], word: "100"}
    assert any("human_nonmoney on a money action" in str(p) for p in validate.validate(d)), word


def test_non_money_keys_are_not_money():
    assert export_rules._has_money_key({"vendor_id": "v", "reason": "x", "exception": "y"}) is False


# --------------------------------------------------------------------------- M2: deny-list
def test_deny_list_finds_a_non_ascii_name_that_the_json_file_stores_as_escapes(tmp_path):
    doc = example("refunds")
    doc["cases"][0]["input_summary"] = f"Refund for M{U_UML}ller GmbH"
    root = write_dir(tmp_path / "e", doc, page=False)  # ensure_ascii: the file holds "M\\u00fcller"
    assert U_UML not in (root / "export.json").read_text(encoding="utf-8")
    res = kitcheck.run(root, deny=[f"m{U_UML}ller gmbh"])
    assert "DENYLIST_HIT" in {p.code for p in res["problems"]}


def test_deny_list_finds_a_term_in_a_key_and_in_the_embedded_page_data(tmp_path):
    doc = example("refunds")
    doc["approvals"][0]["args"]["acme_marker_canary"] = "x"
    root = write_dir(tmp_path / "k", doc)
    assert "DENYLIST_HIT" in {
        p.code for p in kitcheck.run(root, deny=["acme_marker_canary"])["problems"]
    }
    (root / "export.json").write_text(
        json.dumps(example("refunds")), encoding="utf-8"
    )  # page still has it
    hit = [
        p
        for p in kitcheck.run(root, deny=["acme_marker_canary"])["problems"]
        if p.code == "DENYLIST_HIT"
    ]
    assert hit, "the term inside the page's embedded data must still be found"


def test_deny_list_message_names_the_term_number_not_the_term(tmp_path):
    doc = example("refunds")
    doc["cases"][0]["input_summary"] = "other client Contoso"
    res = kitcheck.run(write_dir(tmp_path / "n", doc, page=False), deny=["nothing", "contoso"])
    msgs = [p.message for p in res["problems"] if p.code == "DENYLIST_HIT"]
    assert msgs == ["deny-list term #2 appears in the export"]


def test_blank_only_deny_list_scans_nothing_and_says_so(tmp_path):
    root = write_dir(tmp_path / "b", example("refunds"), page=False)
    res = kitcheck.run(root, deny=["", "   ", "\n"])
    assert res["ok"] and any("no effective term" in n for n in res["notes"])
    assert not any("no deny-list given" in n for n in res["notes"])


def test_cli_deny_list_with_a_byte_order_mark_still_matches_its_first_term(tmp_path):
    doc = example("refunds")
    doc["cases"][0]["input_summary"] = "Refund for Fabrikam"
    root = write_dir(tmp_path / "bom", doc, page=False)
    terms = tmp_path / "terms.txt"
    terms.write_bytes(
        b"\xef\xbb\xbfFabrikam\nSomeoneElse\n"
    )  # UTF-8 BOM, as Windows editors write it
    env = {**os.environ, "PYTHONPATH": str(KIT)}
    out = subprocess.run(
        [sys.executable, "-m", "hadavin_kit.kitcheck", str(root), "--deny-list", str(terms)],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )  # fmt: skip
    assert out.returncode == 1 and "DENYLIST_HIT" in out.stdout


# --------------------------------------------------------------------------- M3: namespace
@pytest.mark.parametrize(
    ("namespace", "ok"),
    [
        ("refunds-demo-tenant", True),
        ("refunds-demo-tenant.demo", True),
        ("refunds-demo-tenant-corp-prod", False),
        ("refunds-demo-tenan", False),
    ],
)
def test_namespace_is_the_tenant_or_the_tenant_then_a_dot(namespace, ok):
    d = example("refunds")
    d["segregation"]["namespace"] = namespace
    assert (not any("namespace" in str(p) for p in validate.validate(d))) is ok


# --------------------------------------------------------------------------- N1, N2, N5
def test_expect_head_detects_truncation_and_reseal(tmp_path):
    from hadavin_kit import chain

    d = example("refunds")
    head = d["audit"]["events"][-1]["sha256"]
    root = write_dir(tmp_path / "h", d, page=False)
    assert kitcheck.run(root, expect_head=head)["ok"]
    assert not any("expect-head" in n for n in kitcheck.run(root, expect_head=head)["notes"])
    assert any("no --expect-head" in n for n in kitcheck.run(root)["notes"])
    cut = copy.deepcopy(
        d
    )  # drop the last events and seal what is left: verifies "N of N", head differs
    cut["audit"]["events"] = cut["audit"]["events"][:-3]
    for c in cut["cases"]:
        c["audit_event_ids"] = [
            e for e in c["audit_event_ids"] if e in {x["event_id"] for x in cut["audit"]["events"]}
        ]
    chain.reseal(cut)
    res = kitcheck.run(write_dir(tmp_path / "c", cut, page=False), expect_head=head)
    assert any(p.code == "CHAIN" and "expect-head" in p.message for p in res["problems"])


def page_edit(tmp_path, name, fn):
    root = write_dir(tmp_path / name, example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(fn(page), encoding="utf-8")
    return root


def test_page_with_an_extra_csp_source_is_refused(tmp_path):
    root = page_edit(
        tmp_path, "src", lambda p: p.replace("script-src 'sha256-", "script-src * 'sha256-", 1)
    )
    assert any("CSP differs" in p.message for p in kitcheck.run(root)["problems"])


def test_page_missing_a_csp_directive_is_refused(tmp_path):
    root = page_edit(tmp_path, "dir", lambda p: p.replace("base-uri 'none'; ", "", 1))
    assert any("CSP differs" in p.message for p in kitcheck.run(root)["problems"])


def test_second_csp_meta_is_refused(tmp_path):
    root = page_edit(
        tmp_path,
        "two",
        lambda p: p.replace(
            "<title>",
            '<meta http-equiv="Content-Security-Policy" content="default-src *"><title>',
            1,
        ),
    )
    assert any("not a console page" in p.message for p in kitcheck.run(root)["problems"])


def test_a_different_script_with_its_own_matching_hash_is_not_the_kits_console(tmp_path):
    import base64
    import hashlib

    def swap(page):
        js = build_console.assets()[2]
        evil = "document.title='x';"
        h_old = "'sha256-" + base64.b64encode(hashlib.sha256(js.encode()).digest()).decode() + "'"
        h_new = "'sha256-" + base64.b64encode(hashlib.sha256(evil.encode()).digest()).decode() + "'"
        return page.replace(h_old, h_new).replace(js, evil, 1)

    root = page_edit(tmp_path, "evil", swap)
    msgs = [p.message for p in kitcheck.run(root)["problems"]]
    assert not any(
        "hash is not in the page CSP" in m for m in msgs
    )  # the page agrees with itself ...
    assert any(
        "differs from the kit's console" in m for m in msgs
    )  # ... but it is not the kit's script


def test_embedded_data_must_match_type_for_type(tmp_path):
    d = example("refunds")
    seed = d["run"]["seed"]
    root = page_edit(
        tmp_path, "types", lambda p: p.replace(f'"seed":{seed}', f'"seed":{seed}.0', 1)
    )
    assert any("embedded data differs" in p.message for p in kitcheck.run(root)["problems"])


def test_readme_is_not_in_the_export_manifest(tmp_path):
    root = write_dir(tmp_path / "r", example("refunds"), page=False)
    (root / "README.md").write_text("hello", encoding="utf-8")
    assert "UNEXPECTED_FILE" in {p.code for p in kitcheck.run(root)["problems"]}


def test_report_hash_mismatch_has_its_own_code_and_a_hash_only_note(tmp_path):
    import hashlib

    doc = example("refunds")
    report = b'{"x": 1}'
    doc["ledger"].update(scope="load_run", report_sha256=hashlib.sha256(report).hexdigest())
    root = write_dir(tmp_path / "rp", doc, page=False)
    (root / "report.json").write_bytes(report)
    res = kitcheck.run(root)
    assert res["ok"] and any("sha256 only" in n for n in res["notes"])
    (root / "report.json").write_bytes(report + b"\n")
    assert "REPORT_MISMATCH" in {p.code for p in kitcheck.run(root)["problems"]}


def test_green_test_double_is_refused_in_a_deploy_directory(tmp_path):
    from hadavin_kit import strict_json

    td = strict_json.load_object(KIT / "demo_only" / "fixtures" / "green-testdouble.json")
    td["tenant_id"] = td["tenant_id"]
    res = kitcheck.run(write_dir(tmp_path / "td", td, page=False))
    assert any("test double" in p.message for p in res["problems"])


# --------------------------------------------------------------------------- N4: printable messages
def test_hostile_keys_never_reach_messages_as_raw_control_characters():
    d = example("refunds")
    d["approvals"][0]["args"]["a\x1b[31m\nb"] = 1
    d["approvals"][0]["args"]["ts" + chr(0x202E)] = 1
    for p in validate.validate(d):
        text = str(p)
        assert not any(ord(c) < 32 or ord(c) in (0x202E, 0x200B) for c in text), ascii(text)


# --------------------------------------------------------------------------- design M2, M3
def test_roles_must_be_stated_in_the_hashed_approval_event_summary():
    d = example("apexc")
    a = next(x for x in d["approvals"] if x["case_id"] == "apexc-h-003")
    assert a["approvers"][0]["roles_at_decision"] == [
        "finance_controller",
        "ap_manager",
    ]  # a two-role approver exists
    assert validate.validate(d) == []
    a["approvers"][0]["roles_at_decision"].append(
        "ceo"
    )  # declared but never stated by the hashed event
    assert any("not stated by the hashed approval event" in str(p) for p in validate.validate(d))


def test_exposed_may_exceed_breached_and_not_the_other_way_round():
    d = example("intake")
    exp = d["scorecard"]["hard_zero_exposure"]["erp_write"]
    assert exp["exposed"] == 1 and d["scorecard"]["hard_zeros"]["erp_write"] == 0
    assert validate.validate(d) == []
    d["scorecard"]["hard_zero_exposure"]["erp_write"]["of"] = 9999
    assert any("is impossible" in str(p) for p in validate.validate(d))


# --------------------------------------------------------------------------- schema text claims
def test_schema_says_sig_scheme_is_a_label_and_roles_are_unauthenticated():
    s = validate.schema()["$defs"]["approval"]["properties"]["approvers"]["items"]["properties"]
    assert (
        "LABEL" in s["sig_scheme"]["description"]
        and "not verification" in s["sig_scheme"]["description"]
    )
    assert "NOT covered by the audit hash" in s["roles_at_decision"]["description"]


def test_deny_list_decodes_the_embedded_page_data_even_when_export_json_is_clean(tmp_path):
    dirty = example("refunds")
    dirty["cases"][0]["input_summary"] = f"Refund for M{U_UML}ller GmbH"
    root = write_dir(tmp_path / "pg", dirty)  # the page embeds the name as an escape
    (root / "export.json").write_text(
        json.dumps(example("refunds")), encoding="utf-8"
    )  # export.json is clean
    assert U_UML not in (root / "index.html").read_text(encoding="utf-8")
    hits = [
        p
        for p in kitcheck.run(root, deny=[f"m{U_UML}ller gmbh"])["problems"]
        if p.code == "DENYLIST_HIT"
    ]
    assert hits
