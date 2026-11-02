"""Console for schema minor 1: honest status equals the Python reference, hostile text stays text, CSP/_headers agree."""

import base64
import hashlib
import re
import sys

import pytest
from hadavin_kit import chain, honest, testing
from hadavin_kit.testing import KIT, SLUGS, example

sys.path.insert(0, str(KIT / "console"))
import build_console

from test_console import BROWSER, _missing, dump

PAYLOAD = (
    '<img src=x onerror="document.title=1"> </script><script>document.title=2</script> <!-- &amp;'
)


def page(tmp_path, doc, name, *, rules=True):
    p = tmp_path / f"{name}.html"
    p.write_text(build_console.build(doc, rules=rules), encoding="utf-8")
    return p


def honest_ids(dom):
    m = re.search(r'data-honest="([^"]*)"', dom)
    assert m, "data-honest missing"
    return set(filter(None, m.group(1).split(",")))


def visible(dom):
    """The rendered page without the embedded data and script text (a dumped DOM contains both)."""
    start = dom.index("<body")
    return dom[start : dom.index("<script", start)]


def need_browser():
    if BROWSER is None:
        _missing("a Chromium-family browser")


# ------------------------------------------------------------------------------------------ no browser needed
def test_headers_file_and_meta_csp_come_from_one_function_and_match_the_sources():
    template, css, js = build_console.assets()
    csp = build_console.csp_for(js, css)
    want = lambda text: (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
    )
    assert f"script-src {want(js)}" in csp and f"style-src {want(css)}" in csp
    headers = build_console.headers_file(csp)
    assert f"Content-Security-Policy: {csp}; frame-ancestors 'none'" in headers
    doc = example("refunds")
    meta = re.search(r'Content-Security-Policy" content="([^"]+)"', build_console.build(doc)).group(
        1
    )
    assert meta == csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "http" not in csp
    del template


def test_csp_hash_changes_when_the_script_changes(monkeypatch):
    base = build_console.csp_for("a", "b")
    assert build_console.csp_for("a ", "b") != base and build_console.csp_for("a", "b ") != base


def test_console_sources_contain_no_html_sinks_for_the_new_pages():
    src = (KIT / "console" / "app.js").read_text(encoding="utf-8")
    assert "pageControls" in src and "innerHTML" not in src and "insertAdjacentHTML" not in src


def test_python_reference_for_honest_status_on_edge_cases():
    d = example("askdata")
    assert honest.status_ids(d)[:2] == ["fixture", "scripted"]
    d["scorecard"]["verdict"] = "green"
    d["scorecard"]["scripted"] = True
    assert "capped_verdict" in honest.status_ids(d) and honest.shown_verdict(d) == "amber"
    d["honesty"]["kind"] = "run"
    d["run"].update(mode="live", model="m")
    d["scorecard"]["scripted"] = False
    ids = honest.status_ids(d)
    assert "fixture" not in ids and "scripted" not in ids
    d["ledger"]["missing"] = 1
    d["segregation"]["canary"] = {"ran": True, "foreign_hits": 1}
    d["provider_terms"].update(no_training_setting="excluded", checked_on="2026-11-01")
    d["load_profile"].update(label="measured", report_sha256="a" * 64)
    ids = honest.status_ids(d)
    assert {"ledger_breach", "canary_breach", "load_measured"} <= set(
        ids
    ) and "terms_unrecorded" not in ids
    d["audit"]["events"][0]["summary"] += "x"
    assert "chain_bad" in honest.status_ids(d)
    lie = example("refunds")
    lie["audit"]["verify"]["matched"] -= 1  # a false claim about a chain that is in fact fine
    assert "chain_bad" in honest.status_ids(lie)
    assert chain.verify(d["audit"]["events"], d["audit"]["chain"])["ok"] is False


# ------------------------------------------------------------------------------------------ browser
@pytest.mark.browser
@pytest.mark.parametrize("slug", SLUGS)
def test_honest_ids_in_the_page_equal_the_python_reference(tmp_path, slug):
    need_browser()
    doc = example(slug)
    dom = dump(page(tmp_path, doc, slug), "controls", "light")
    assert 'data-js-errors="0"' in dom and 'data-csp-violations="0"' in dom
    assert honest_ids(dom) == set(honest.status_ids(doc))


@pytest.mark.browser
def test_a_false_chain_claim_is_flagged_the_same_way_in_page_and_reference(tmp_path):
    need_browser()
    doc = example("refunds")
    doc["audit"]["verify"]["matched"] -= 1
    doc["audit"]["verify"]["ok"] = False
    dom = dump(page(tmp_path, doc, "lie", rules=False), "controls", "light")
    assert honest_ids(dom) == set(honest.status_ids(doc)) and "chain_bad" in honest_ids(dom)


@pytest.mark.browser
def test_breaches_and_unrecorded_terms_are_shown_never_as_a_pass(tmp_path):
    need_browser()
    doc = example("refunds")
    doc["ledger"]["missing"] = 2
    doc["ledger"]["reconciliation_gap"] = 2
    doc["segregation"]["canary"] = {"ran": True, "foreign_hits": 3}
    dom = dump(page(tmp_path, doc, "breach"), "controls", "dark")
    assert honest_ids(dom) == set(honest.status_ids(doc))
    v = visible(dom)
    assert "LEDGER BREACH" in v and "foreign canary found: 3" in v and "NOT RECORDED" in v
    assert "ASSUMED" in v and "No breach is stated" not in v
    ok = example("refunds")
    ok["segregation"]["canary"] = {"ran": True, "foreign_hits": 0}
    dom2 = dump(page(tmp_path, ok, "ok"), "controls", "light")
    v2 = visible(dom2)
    assert (
        "No breach is stated in this fixture" in v2
        and "ran, 0 foreign hits" in v2
        and "LEDGER BREACH" not in v2
    )


@pytest.mark.browser
def test_scripted_run_is_never_green_and_says_why(tmp_path):
    need_browser()
    doc = example("askdata")
    doc["scorecard"]["verdict"] = (
        "green"  # invalid on purpose: built without validation to test the page itself
    )
    dom = dump(page(tmp_path, doc, "g", rules=False), "scorecard", "light")
    v = visible(dom)
    assert "scripted; gate vs gold agreement; not model accuracy" in v
    assert "capped_verdict" in honest_ids(dom) and "Plumbing check only" in v
    assert "Demonstrated (smoke)" not in v


@pytest.mark.browser
def test_new_text_fields_render_as_text_not_markup(tmp_path):
    need_browser()
    doc = example("apexc")
    doc["load_profile"]["source"] = PAYLOAD
    doc["read_log"]["by_purpose"][0]["purpose"] = PAYLOAD
    doc["provider_terms"].update(
        provider=PAYLOAD, no_training_setting=PAYLOAD, checked_on="2026-11-01"
    )
    case = testing.case(doc, "apexc-a-002")
    case["terminal_state"]["reason"] = PAYLOAD
    doc["approvals"][0]["evidence"][0]["value"] = PAYLOAD
    html = page(tmp_path, doc, "xss")
    for route in ("controls", f"run?case={case['case_id']}", "approvals", "scorecard"):
        dom = dump(html, route, "light")
        assert 'data-js-errors="0"' in dom and 'data-csp-violations="0"' in dom, route
        assert "<img" not in dom.replace("&lt;img", ""), route
        assert "<title>1</title>" not in dom and "<script>document.title=2" not in dom, route
    assert "&lt;img src=x onerror" in visible(dump(html, "controls", "light"))


@pytest.mark.browser
def test_new_case_and_approval_fields_are_visible(tmp_path):
    need_browser()
    doc = example("apexc")
    html = page(tmp_path, doc, "a")
    run = visible(dump(html, "run?case=apexc-a-002", "light"))
    assert "message ended" in run and "controller_review" in run and "rcpt-apexc-a-002" in run
    override = visible(dump(html, "run?case=apexc-h-003", "light"))
    assert "controller override" in override
    nonmoney = visible(dump(html, "run?case=apexc-e-004", "light"))
    assert "human nonmoney" in nonmoney
    dd = visible(dump(page(tmp_path, example("dealdesk"), "d"), "approvals", "light"))
    assert "shared-secret HMAC" in dd and "Decision bound to" in dd and "Barred from deciding" in dd
    sc = visible(dump(page(tmp_path, example("refunds"), "r"), "scorecard", "light"))
    assert "Steered replays denied" in sc and "cases exposed" in sc
    sc2 = visible(dump(page(tmp_path, example("apexc"), "a2"), "scorecard", "light"))
    assert "False hold" in sc2


SENTENCE = "scripted; gate vs gold agreement; not model accuracy"


@pytest.mark.browser
def test_scripted_sentence_is_in_the_banner_of_every_page_and_scorecard_never_says_promising(
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor

    need_browser()
    pages = {s: page(tmp_path, example(s), s) for s in SLUGS}
    jobs = [
        (s, r) for s in SLUGS for r in ("approvals", "run", "scorecard", "registry", "controls")
    ]
    with ThreadPoolExecutor(6) as ex:
        doms = dict(
            zip(
                jobs, ex.map(lambda j: visible(dump(pages[j[0]], j[1], "light")), jobs), strict=True
            )
        )
    for (slug, route), v in doms.items():
        assert SENTENCE in v, (slug, route)
        assert "Promising, not demonstrated" not in v, (slug, route)
    for slug in SLUGS:
        sc = doms[(slug, "scorecard")]
        assert sc.count(SENTENCE) >= 4, (
            slug
        )  # banner, verdict note, rate block and safety-number block
        if example(slug)["scorecard"]["verdict"] == "amber":
            assert "amber (plumbing only)" in sc, slug


@pytest.mark.browser
def test_sig_scheme_other_than_hmac_is_shown_as_an_unverified_label(tmp_path):
    need_browser()
    doc = example("dealdesk")
    doc["approvals"][0]["approvers"][0]["sig_scheme"] = "ed25519"
    v = visible(dump(page(tmp_path, doc, "sig"), "approvals", "light"))
    assert "scheme label: ed25519" in v and "cannot verify it" in v
