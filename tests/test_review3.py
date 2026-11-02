"""Review round 2 (code-2 and design-2 of PR 5 at 2810d7d). The behavioural tests here failed on that head. A few are
guards that assert TEXT or a FUTURE condition, not behaviour, and are labelled so: `test_export_chain_is_labelled...`,
`test_not_demonstrated_is_a_neutral_tone...` (string presence in app.js/app.css; the browser tests cover the rendering),
`test_build_console_is_a_mutation_target...`, `test_contract_documents...` (documentation present) and
`test_the_one_time_exceptions_expire...` (a tripwire that bites when minor 1 is frozen)."""

import json
import shutil
import subprocess
import sys

import pytest
from hadavin_kit import (
    chain,
    export_rules,
    kitcheck,
    scripted_llm,
    textnorm,
    validate,
    versioning,
)
from hadavin_kit.testing import KIT, SLUGS, example

sys.path.insert(0, str(KIT / "console"))
sys.path.insert(0, str(KIT / "tools"))
import build_console

import freeze
import mutation

APP = (KIT / "console" / "app.js").read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------- code M3: money keys by word
@pytest.mark.parametrize(
    "name",
    ["amount_usd", "fee_eur", "total_cents", "price_gbp", "refund_amount_usd", "amountUsd", "feeUSD", "TotalMinor",
     "payout-iban", "customerAmount", "net.amount", "sum", "total_minor", "balance"],
)  # fmt: skip
def test_money_names_are_found_word_by_word(name):
    assert export_rules.is_money_name(name), name


@pytest.mark.parametrize(
    "name",
    [
        "checksum",
        "vendor_id",
        "reason",
        "exception",
        "discount_pct",
        "summary",
        "currency",
        "order_id",
        "feedback",
        "valued",
    ],
)
def test_names_that_merely_contain_a_money_word_are_not_money(name):
    assert not export_rules.is_money_name(name), name


def test_money_search_catches_unit_suffixed_keys_in_args():
    d = example("apexc")
    a = next(x for x in d["approvals"] if x["tier"] == "human_nonmoney")
    for key in ("fee_usd", "amountUsd", "amount_cents", "price_gbp"):
        broken = json.loads(json.dumps(d))
        next(x for x in broken["approvals"] if x["approval_id"] == a["approval_id"])["args"][
            key
        ] = "5"
        assert any(
            "human_nonmoney on a money action" in str(p) for p in validate.validate(broken)
        ), key


def test_javascript_and_python_agree_on_money_names():
    names = [
        "amount_usd",
        "fee_eur",
        "amountUsd",
        "checksum",
        "vendor_id",
        "Total_Minor",
        "feeUSD",
        "summary",
        "iban",
        "x",
        "",
        "amounts",
        "total2",
        "totalusd",
        "refundamount",
        "amt",
        "feeusd",
        "account_id",
        "value",
        "cost",
        "amateur",
    ]
    script = (
        "const m=require(process.argv[1]);"
        "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(n=>[m.moneyName(n), m.nameTokens(n)])));"
    )
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    out = subprocess.run(
        [node, "-e", script, str(KIT / "console" / "chain.js"), json.dumps(names)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    got = json.loads(out.stdout)
    for n, (is_money, toks) in zip(names, got, strict=True):
        assert is_money == export_rules.is_money_name(n), n
        assert toks == textnorm.tokens(n), n


def test_textnorm_helpers():
    assert textnorm.tokens("refund_amount_usd") == ["refund", "amount", "usd"]
    assert textnorm.tokens("amountUSD") == ["amount", "usd"]
    assert textnorm.tokens("HTTPServer2Go") == ["http", "server2", "go"]
    assert textnorm.alnum("Refund-Issue_2") == "refundissue2"
    assert textnorm.fold("\ufeffMÜLLER") == textnorm.fold("müller")


def test_the_two_fold_helpers_are_one():
    for name in ("kitcheck.py", "rules_v11.py", "export_rules.py"):
        src = (KIT / "hadavin_kit" / name).read_text(encoding="utf-8")
        assert "def _fold" not in src, name


# --------------------------------------------------------------------------- code M2: the whole page, not just parts
def edit_page(tmp_path, name, fn, doc=None):
    root = write_dir(tmp_path / name, doc or example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    (root / "index.html").write_text(fn(page), encoding="utf-8")
    return root


def full_page_problem(root):
    return any(
        "differs from the page the kit builds" in p.message for p in kitcheck.run(root)["problems"]
    )


def test_a_hand_edited_skeleton_is_found(tmp_path):
    inserted = edit_page(
        tmp_path,
        "ins",
        lambda p: p.replace("<body>", "<body><div>Verified by Hadavin, production</div>", 1),
    )
    assert full_page_problem(inserted)
    unhidden = edit_page(
        tmp_path,
        "unhide",
        lambda p: p.replace(' id="ribbon" role="note" hidden', ' id="ribbon" role="note"', 1),
    )
    assert full_page_problem(unhidden)
    refresh = edit_page(
        tmp_path,
        "refresh",
        lambda p: p.replace(
            "<head>", '<head><meta http-equiv="refresh" content="0;url=other.html">', 1
        ),
    )
    assert full_page_problem(refresh)
    banner = edit_page(
        tmp_path, "banner", lambda p: p.replace("prototype, sample data", "production data", 1)
    )
    assert full_page_problem(banner)


def test_a_url_inside_a_case_summary_is_not_a_remote_reference(tmp_path):
    doc = example("refunds")
    doc["cases"][0]["input_summary"] = "see https://docs.acme.example/policy and the url(x) helper"
    res = kitcheck.run(write_dir(tmp_path / "u", doc))
    assert not any("remote URL" in p.message for p in res["problems"])
    assert res["ok"], [str(p) for p in res["problems"]]


def test_a_remote_reference_in_the_skeleton_is_still_refused(tmp_path):
    root = edit_page(
        tmp_path,
        "r",
        lambda p: p.replace(
            "<head>", '<head><link rel="stylesheet" href="https://cdn.example.org/a.css">', 1
        ),
    )
    assert any("remote URL" in p.message for p in kitcheck.run(root)["problems"])


# --------------------------------------------------------------------------- code M4: every .json is decoded
def test_deny_list_decodes_report_json_too(tmp_path):
    root = write_dir(tmp_path / "rp", example("refunds"), page=False)
    (root / "report.json").write_text(
        json.dumps({"note": "Refund for Müller GmbH"}), encoding="utf-8"
    )  # ensure_ascii
    assert "ü" not in (root / "report.json").read_text(encoding="utf-8")
    res = kitcheck.run(root, deny=["müller gmbh"])
    assert "DENYLIST_HIT" in {p.code for p in res["problems"]}
    (root / "report.json").write_text("{not json", encoding="utf-8")
    again = kitcheck.run(root, deny=["zzz-nothing"])
    assert any("not strict JSON" in n for n in again["notes"])


# --------------------------------------------------------------------------- design M2: roles grammar, word boundaries
def test_a_stated_role_set_must_equal_the_declared_set_exactly():
    d = example("dealdesk")
    a = d["approvals"][0]
    role = a["approvers"][0]["role"]
    a["approvers"][0]["roles_at_decision"] = [role, "ops"]
    ev = next(
        e
        for e in d["audit"]["events"]
        if e["case_id"] == a["case_id"]
        and e["actor"] == f"human:{role}"
        and e["type"] == "approval"
    )
    ev["summary"] = ev["summary"].split(" [roles=")[0] + " stops operations [roles=" + role + "]"
    chain.reseal(d)
    msgs = [str(p) for p in validate.validate(d)]
    assert any("roles_at_decision of" in m and "ops" in m for m in msgs), msgs


def test_the_marker_must_be_at_the_end_of_the_summary():
    d = example("apexc")
    ev = next(
        e
        for e in d["audit"]["events"]
        if e["case_id"] == "apexc-h-003" and e["type"] == "approval" and "[roles=" in e["summary"]
    )
    ev["summary"] = ev["summary"] + " (late note)"
    chain.reseal(d)
    assert any("not stated by the hashed approval event" in str(p) for p in validate.validate(d))


# --------------------------------------------------------------------------- design M3: verifiable exposure
def test_exposure_of_must_equal_the_case_count_or_list_its_cases():
    d = example("refunds")
    d["scorecard"]["hard_zero_exposure"] = {"followed_injection": {"exposed": 1, "of": 10000}}
    assert any("is impossible" in str(p) for p in validate.validate(d))
    d["scorecard"]["hard_zero_exposure"]["followed_injection"] = {"exposed": 1, "of": 3}
    assert any("list the cases at risk" in str(p) for p in validate.validate(d))


# --------------------------------------------------------------------------- design minors
def test_not_demonstrated_is_a_neutral_tone_not_the_breach_red():
    assert "not_demonstrated: 'neutral'" in APP and "not_demonstrated: 'bad'" not in APP
    css = (KIT / "console" / "app.css").read_text(encoding="utf-8")
    assert ".verdict-not_demonstrated { border-left: 6px solid var(--muted); }" in css


def test_export_chain_is_labelled_a_copy_on_the_run_page():
    assert (
        "Export chain only" in APP
        and "no tamper-evidence claim about the audit rests on this page" in APP
    )


def test_scripted_run_must_carry_the_recorded_turns_hash(tmp_path):
    d = example("refunds")
    assert len(d["run"]["recorded_turns_sha256"]) == 64
    recorded = tmp_path / "recorded"
    (recorded / "x-e-001").mkdir(parents=True)
    (recorded / "x-e-001" / "turn-1.json").write_text('{"reply": "a"}', encoding="utf-8")
    h1 = scripted_llm.recorded_turns_sha256(recorded)
    assert h1 == scripted_llm.recorded_turns_sha256(recorded) and len(h1) == 64
    (recorded / "x-e-001" / "turn-1.json").write_text('{"reply": "b"}', encoding="utf-8")
    assert scripted_llm.recorded_turns_sha256(recorded) != h1
    (recorded / "x-e-001" / "turn-1.json").rename(recorded / "x-e-001" / "turn-2.json")
    assert scripted_llm.recorded_turns_sha256(recorded) not in (
        h1,
    )  # a renamed file is a different set


def test_case_ids_are_plain_slugs_now():
    d = example("refunds")
    d["cases"][0]["case_id"] = "case.2026-11-01.alpha"
    old_id = "refunds-e-001"
    for a in d["approvals"]:
        a["case_id"] = "case.2026-11-01.alpha" if a["case_id"] == old_id else a["case_id"]
    for e in d["audit"]["events"]:
        e["case_id"] = "case.2026-11-01.alpha" if e["case_id"] == old_id else e["case_id"]
    chain.reseal(d)
    assert not [p for p in validate.validate(d) if p.code in ("BAD_VALUE", "UNKNOWN_KEY")]
    d["cases"][0]["case_id"] = "Bad Id"
    assert any(p.code == "BAD_VALUE" for p in validate.validate(d))


# --------------------------------------------------------------------------- freeze per minor, expiry of the exceptions
@pytest.mark.needs_samples
def test_freeze_copies_the_set_once_and_never_rewrites_it(tmp_path):
    kit = tmp_path / "kit"
    for sub in ("demo_only/examples", "demo_only/fixtures", "schema"):
        (kit / sub).mkdir(parents=True)
    for slug in SLUGS:
        shutil.copyfile(
            KIT / "demo_only" / "examples" / f"{slug}.json",
            kit / "demo_only" / "examples" / f"{slug}.json",
        )
    shutil.copyfile(
        KIT / "demo_only" / "fixtures" / "green-testdouble.json",
        kit / "demo_only" / "fixtures" / "green-testdouble.json",
    )
    shutil.copyfile(KIT / "schema" / "demo-export.v1.json", kit / "schema" / "demo-export.v1.json")
    dest = freeze.freeze(kit, 1)
    assert dest.name == "compat-v1.1" and (dest / "demo-export.v1.1.schema.json").is_file()
    lines = (dest / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(SLUGS) + 2
    assert freeze.frozen_dirs(kit) == {1: dest}
    with pytest.raises(SystemExit, match="never rewritten"):
        freeze.freeze(kit, 1)


@pytest.mark.needs_samples
def test_every_frozen_minor_constrains_the_current_schema():
    """Each frozen compat-v1.N schema (minor 0 always) must stay additive against the current one."""
    current = validate.schema()
    frozen = freeze.frozen_dirs(KIT)
    assert 0 in frozen
    for minor, d in frozen.items():
        old = json.loads((d / f"demo-export.v1.{minor}.schema.json").read_text(encoding="utf-8"))
        accepted = versioning.ACCEPTED_TIGHTENINGS if minor == 0 else {}
        assert versioning.additive_diff(old, current, accepted) == [], minor
        for p in d.glob("*.json"):
            if p.name.endswith(".schema.json"):
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert validate.validate(doc) == [], (minor, p.name)


def test_the_one_time_exceptions_expire_when_minor_one_is_frozen():
    if 1 in freeze.frozen_dirs(KIT):
        assert versioning.ACCEPTED_TIGHTENINGS == {}, (
            "the unreleased-minor-0 exceptions are only valid until minor 1 is frozen"
        )
    else:  # not vacuous before the freeze: the list must still be there for the diff to be accepted
        assert len(versioning.ACCEPTED_TIGHTENINGS) == 8


# --------------------------------------------------------------------------- mutation harness additions
def test_test_files_list_has_no_duplicates_and_every_file_exists():
    assert len(mutation.TEST_FILES) == len(set(mutation.TEST_FILES))
    for name in mutation.TEST_FILES:
        assert (KIT / "tests" / name).is_file(), name


def test_new_operators_exist_and_change_the_source():
    kinds = set()
    for t in ("kitcheck.py", "console/build_console.py", "export_rules.py", "versioning.py"):
        kinds |= {m.kind.split("[")[0] for m in mutation.enumerate_python(t)}
    assert {"literal drop", "directive drop", "regex alternation drop"} <= kinds
    for t in ("kitcheck.py", "console/build_console.py"):
        original = mutation.ast.unparse(
            mutation.ast.parse(mutation.src_path(t).read_text(encoding="utf-8"))
        )
        for m in mutation.enumerate_python(t):
            if m.kind.startswith(("literal", "directive", "regex")):
                assert mutation.mutate_python(t, m.index) != original, (t, m.kind, m.text[:50])


def test_regex_helpers():
    assert mutation._split_alts("a|b(c|d)|e[f|g]") == ["a", "b(c|d)", "e[f|g]"]
    assert mutation._split_alts(r"a\|b|c") == [r"a\|b", "c"]
    assert [m.group(1) for m in mutation._GROUP.finditer("(x|y)z(?:p|q)")] == ["x|y", "p|q"]


def test_build_console_is_a_mutation_target_and_js_is_declared_excluded():
    assert "console/build_console.py" in mutation.DEFAULT_TARGETS
    src = (KIT / "tools" / "mutation.py").read_text(encoding="utf-8")
    assert "NOT mutated, on purpose" in src and "chain.js" in src and "app.js" in src


# --------------------------------------------------------------------------- CI and docs
CI = KIT / ".github" / "workflows" / "ci.yml"


def test_gitleaks_scans_the_whole_history_and_compares_the_scanned_count():
    if not CI.is_file():
        pytest.skip("repository root not present")
    text = CI.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text and "set -euo pipefail" in text
    assert "git rev-list --count HEAD" in text and "commits scanned" in text
    assert "origin/main..HEAD" not in text, (
        "the whole history is scanned, on pushes and pull requests"
    )
    assert "schedule:" in text and "push:" in text and "pull_request:" in text
    assert "uv sync --locked" in text and "contents: read" in text
    assert "3.12" in text and "3.13" in text and "rm -rf demo" in text


def test_ci_runs_every_fixture_through_kitcheck_as_a_matrix():
    if not CI.is_file():
        pytest.skip("repository root not present")
    text = CI.read_text(encoding="utf-8")
    assert "fixtures:" in text and "matrix:" in text and "tools/check_fixture.py" in text
    for slug in SLUGS:
        assert slug in text


def test_contract_documents_the_vendoring_and_minor_bump_procedure():
    doc = (KIT / "CONTRACT.md").read_text(encoding="utf-8")
    for needle in (
        "## Vendoring",
        "KIT.lock",
        "kitsum --check",
        "## Bumping the kit minor",
        "hadavin-sdk",
        "kit console",
        "export chain",
        "audit.core_anchor",
        "Freeze-time checklist",
        "compat-v1.",
        "Minor-2 candidates",
    ):
        assert needle in doc, needle
    assert (KIT / "CORE-COPIES.md").is_file()
    assert "evalcore" in (KIT / "CORE-COPIES.md").read_text(encoding="utf-8")
