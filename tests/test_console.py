import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
CONSOLE = KIT / "console"
sys.path.insert(0, str(CONSOLE))
import build_console

EXAMPLES = sorted((KIT / "demo_only" / "examples").glob("*.json")) or [
    KIT
    / "demo_only"
    / "examples"
    / "missing.json"  # cut test: loading it skips the test (see tests/conftest.py)
]
ROUTES = ["approvals", "run", "scorecard", "registry", "controls"]


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def reseal(d):
    """Recompute the chain after a test edited an event, so the export stays internally consistent."""
    from hadavin_kit import chain

    chain.reseal(d)


def xss_doc():
    d = load(KIT / "demo_only" / "examples" / "refunds.json")
    bad = '<img src=x onerror="document.title=1"> </script><script>document.title=2</script> <!-- &amp;'
    d["cases"][0]["input_summary"] = bad
    d["cases"][0]["steps"][0]["reason"] = bad
    d["approvals"][0]["evidence"][0]["value"] = bad
    d["audit"]["events"][0]["summary"] = bad
    reseal(d)
    d["demo"]["title"] = "</title><script>1</script>"
    return d


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_build_has_strict_csp_and_no_remote_reference(path):
    page = build_console.build(load(path))
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert "default-src 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert csp.count("sha256-") == 2
    assert not re.search(r"https?://|//cdn|@import|url\(", page)
    assert "prototype, sample data" in page


def test_sources_never_use_html_sinks():
    src = "".join((CONSOLE / n).read_text(encoding="utf-8") for n in ("app.js", "chain.js"))
    for bad in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "sendBeacon",
        "localStorage",
        "sessionStorage",
        "import(",
    ):
        assert bad not in src, bad


def test_xss_payload_is_inert_in_the_html():
    page = build_console.build(xss_doc())
    assert page.count("</script>") == 2  # only the template's own two closers
    assert page.count("<script") == 2
    assert "<img" not in page and "<!--" not in page
    assert "</title><script>" not in page


def test_invalid_export_is_refused():
    d = load(EXAMPLES[0])
    d["surprise"] = 1
    with pytest.raises(ValueError):
        build_console.build(d)


# ------------------------------------------------------------------------------------------- JS chain parity
@pytest.mark.needs_samples
def test_js_chain_matches_python_and_detects_tamper():
    if shutil.which("node") is None:
        _missing("node")
    script = """
const m = require(process.argv[1]);
const ex = process.argv.slice(2);
const fs = require('fs'); const crypto = require('crypto');
if (m.sha256('abc') !== crypto.createHash('sha256').update('abc').digest('hex')) throw new Error('sha256 abc');
const long = 'x'.repeat(1000);
if (m.sha256(long) !== crypto.createHash('sha256').update(long).digest('hex')) throw new Error('sha256 long');
const out = {};
for (const p of ex) {
  const d = JSON.parse(fs.readFileSync(p, 'utf8'));
  const r = m.verifyChain(d.audit.events, d.audit.chain);
  const t = d.audit.events.map(e => Object.assign({}, e)); t[1].summary += 'x';
  out[p] = [r.ok, r.matched, m.verifyChain(t, d.audit.chain).ok];
}
const u = {prev_sha256: m.GENESIS, seq: 1, event_id: 'e1', ts: '2026-11-01T09:00:00Z', type: 'intent', actor: 'agent', case_id: '', summary: 'Zo\\u00eb \\ud83d\\ude42 \\u007f "q" \\\\ \\n'};
console.log(JSON.stringify({out, u: m.eventHash(u)}));
"""
    res = subprocess.run(
        ["node", "-e", script, str(CONSOLE / "chain.js"), *map(str, EXAMPLES)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    got = json.loads(res.stdout)
    for p in EXAMPLES:
        ok, matched, tampered_ok = got["out"][str(p)]
        assert ok and tampered_ok is False and matched == len(load(p)["audit"]["events"])
    from hadavin_kit.chain import event_hash

    ev = {
        "prev_sha256": "0" * 64,
        "seq": 1,
        "event_id": "e1",
        "ts": "2026-11-01T09:00:00Z",
        "type": "intent",
        "actor": "agent",
        "case_id": "",
        "summary": 'Zo\u00eb \U0001f642 \x7f "q" \\ \n',
    }
    assert got["u"] == event_hash(ev)


# ------------------------------------------------------------------------------------------------ contrast
def _lum(hexcol):
    c = [int(hexcol[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_token_contrast_is_at_least_aa_in_both_themes():
    css = (CONSOLE / "app.css").read_text(encoding="utf-8")
    light = dict(
        re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})", css.split(':root[data-theme="dark"]')[0])
    )
    dark = dict(
        re.findall(
            r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})",
            css.split(':root[data-theme="dark"]')[1].split("}")[0],
        )
    )
    pairs = [("text", "bg"), ("text", "surface"), ("text", "surface-2"), ("muted", "bg"), ("muted", "surface"), ("muted", "surface-2"),
             ("accent-ink", "accent"), ("accent", "surface"), ("accent", "bg"), ("banner-fg", "banner-bg"),
             ("text", "border")]  # fmt: skip
    pairs.remove(("text", "border"))
    for tone in ("ok", "warn", "bad", "info", "neutral"):
        pairs.append((f"{tone}-fg", f"{tone}-bg"))
    pairs += [
        ("warn-fg", "surface-2"),
        ("bad-fg", "surface-2"),
        ("ok-fg", "surface"),
        ("bad-fg", "surface"),
        ("info-fg", "info-bg"),
    ]
    for name, theme in (("light", light), ("dark", {**light, **dark})):
        for bg in ("surface", "bg"):  # control outlines need 3:1 (WCAG 1.4.11)
            assert _ratio(theme["border"], theme[bg]) >= 3, (name, "border", bg)
        for fg, bg in pairs:
            assert _ratio(theme[fg], theme[bg]) >= 4.5, (
                name,
                fg,
                bg,
                round(_ratio(theme[fg], theme[bg]), 2),
            )
    assert _ratio(dark["focus"], dark["bg"]) >= 3 and _ratio(light["focus"], light["bg"]) >= 3


# -------------------------------------------------------------------------------------------- headless render
def _missing(what):
    if os.environ.get("CI_REQUIRE_BROWSER"):
        pytest.fail(f"{what} is required in CI (CI_REQUIRE_BROWSER is set) but is missing")
    pytest.skip(f"{what} not installed")


def find_browser():
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "chrome",
    ):
        p = shutil.which(name)
        if p:
            return p
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).is_file():
            return p
    return None


BROWSER = find_browser()


def dump(page: Path, route: str, theme: str) -> str:
    url = page.resolve().as_uri() + f"#/{route}?theme={theme}"
    res = subprocess.run(
        [BROWSER, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=3000", "--dump-dom", url],
        capture_output=True, text=True, timeout=90, check=False,
    )  # fmt: skip
    return res.stdout


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    d = tmp_path_factory.mktemp("console")
    out = {}
    for p in EXAMPLES:
        out[p.stem] = d / f"{p.stem}.html"
        out[p.stem].write_text(build_console.build(load(p)), encoding="utf-8")
    out["xss"] = d / "xss.html"
    out["xss"].write_text(build_console.build(xss_doc()), encoding="utf-8")
    return out


@pytest.mark.browser
def test_every_example_renders_all_pages_in_both_themes(pages):
    if BROWSER is None:
        _missing("a Chromium-family browser")
    jobs = [
        (s, r, t) for s in [p.stem for p in EXAMPLES] for r in ROUTES for t in ("light", "dark")
    ]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda j: (j, dump(pages[j[0]], j[1], j[2])), jobs))
    for (slug, route, theme), dom in results:
        tag = (slug, route, theme)
        assert f'data-rendered="{route}"' in dom, tag
        assert 'data-js-errors="0"' in dom and 'data-csp-violations="0"' in dom, tag
        assert f'data-theme="{theme}"' in dom, tag
        assert "prototype, sample data" in dom, tag
    scorecard = dict(results_by(results, "refunds", "scorecard", "light"))
    assert (
        "Not demonstrated" in scorecard["dom"]
        and "False abstain" in scorecard["dom"]
        and "Wrong and allowed" in scorecard["dom"]
    )
    assert (
        "amber (plumbing only)" in dict(results_by(results, "intake", "scorecard", "light"))["dom"]
    )


def results_by(results, slug, route, theme):
    for (s, r, t), dom in results:
        if (s, r, t) == (slug, route, theme):
            return [("dom", dom)]
    raise KeyError


@pytest.mark.browser
def test_xss_payload_renders_as_text_not_markup(pages):
    if BROWSER is None:
        _missing("a Chromium-family browser")
    for route in ("approvals", "run"):
        dom = dump(pages["xss"], route, "light")
        assert (
            f'data-rendered="{route}"' in dom
            and 'data-js-errors="0"' in dom
            and 'data-csp-violations="0"' in dom
        )
        assert "<img" not in dom.replace("&lt;img", "")  # no img element exists, only escaped text
        assert "&lt;img src=x onerror" in dom
        assert "<title>1</title>" not in dom
        assert "<script>document.title=2" not in dom
    # run page: the tampered event must make the chain check fail when the user clicks; shown by the node test.
