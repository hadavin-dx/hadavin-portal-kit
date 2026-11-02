"""Review round 2026-11-01 (two independent reviews of PR #5): each test below fails on the pre-review code."""

import asyncio
import copy
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from hadavin_kit import chain, evalcore, export_rules, strict_json
from hadavin_kit.pytest_plugin import NetworkBlocked
from hadavin_kit.scripted_llm import MissingScript, PromptDrift, ScriptedLLM, prompt_sha256
from jsonschema import Draft202012Validator

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "console"))
import build_console

SCHEMA = json.loads((KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8"))
V = Draft202012Validator(SCHEMA)
SLUGS = ["refunds", "apexc", "intake", "dealdesk", "askdata"]


def doc(slug):
    return copy.deepcopy(strict_json.load_object(KIT / "demo_only" / "examples" / f"{slug}.json"))


def problems(d):
    return [e.message for e in V.iter_errors(d)] + export_rules.check(d)


def case(d, cid):
    return next(c for c in d["cases"] if c["case_id"] == cid)


def reseal(d):
    chain.reseal(d)


def require(tool):
    path = shutil.which(tool) if tool == "node" else tool
    if path is None:
        if os.environ.get("CI_REQUIRE_BROWSER"):
            pytest.fail(f"{tool} is required in CI (CI_REQUIRE_BROWSER is set) but is missing")
        pytest.skip(f"{tool} not installed")
    return path


# ------------------------------------------------------------------------------ 1 scripted truth (blocker)
def test_scripted_run_cannot_be_unmarked_or_green():
    d = doc("askdata")
    d["scorecard"].pop("scripted")
    assert any("scorecard.scripted must be true" in p for p in problems(d))
    d = doc("askdata")
    d["scorecard"].update(scripted=False, verdict="green")
    assert problems(d)
    d = doc("askdata")
    d["run"]["mode"] = "live"  # model still says scripted
    assert any("disagree" in p for p in problems(d))
    d = doc("askdata")
    d["run"]["model"] = "gpt-like"  # mode still says scripted
    assert any("disagree" in p for p in problems(d))


def test_green_testdouble_is_the_only_green_and_is_labelled():
    fx = strict_json.load_object(KIT / "demo_only" / "fixtures" / "green-testdouble.json")
    assert (
        not problems(fx) and fx["scorecard"]["verdict"] == "green" and fx["run"]["mode"] == "live"
    )
    assert fx["honesty"]["kind"] == "fixture" and "TEST DOUBLE" in fx["honesty"]["notes"][0]
    for s in SLUGS:
        assert doc(s)["scorecard"]["verdict"] != "green"


# ------------------------------------------------------------------------------ 2 internal truth
def test_build_refuses_a_chain_that_does_not_verify():
    d = doc("refunds")
    d["audit"]["events"][3]["summary"] += " tampered"
    with pytest.raises(ValueError, match="audit chain"):
        build_console.build(d)
    d = doc("refunds")
    d["audit"]["verify"]["text"] = "local integrity check: 55 of 56 events match"
    d["audit"]["verify"]["matched"] = 55
    d["audit"]["verify"]["ok"] = False
    assert any("claims" in p for p in problems(d))


def mutate(slug, fn):
    d = doc(slug)
    fn(d)
    return d


CASES = {
    "green with a hard zero": lambda: mutate(
        "askdata",
        lambda d: (
            d["run"].update(mode="live", model="x"),
            d["scorecard"].update(scripted=False, verdict="green"),
            d["scorecard"]["hard_zeros"].update(writes_executed=1),
        ),
    ),
    "green above its threshold": lambda: mutate(
        "askdata",
        lambda d: (
            d["run"].update(mode="live", model="x"),
            d["scorecard"].update(scripted=False, verdict="green"),
            d["scorecard"]["confident_wrong"].update(upper_bound=0.5),
        ),
    ),
    "n differs from cases": lambda: mutate("refunds", lambda d: d["scorecard"].update(n=99)),
    "confident_wrong count": lambda: mutate(
        "refunds", lambda d: case(d, "refunds-e-001").update(wrong_confident=True)
    ),
    "false_abstain count": lambda: mutate(
        "refunds", lambda d: d["scorecard"]["false_abstain"].update(x=0)
    ),
    "wrong_and_allowed count": lambda: mutate(
        "refunds", lambda d: d["scorecard"]["wrong_and_allowed"].update(x=3)
    ),
    "hard zero count vs flags": lambda: mutate(
        "dealdesk", lambda d: d["scorecard"]["hard_zeros"].update(quote_without_approvals=0)
    ),
    "duplicate case_id": lambda: mutate(
        "refunds", lambda d: d["cases"][1].update(case_id=d["cases"][0]["case_id"])
    ),
    "duplicate approval_id": lambda: mutate(
        "refunds", lambda d: d["approvals"][1].update(approval_id=d["approvals"][0]["approval_id"])
    ),
    "duplicate event_id": lambda: mutate(
        "refunds",
        lambda d: d["audit"]["events"][1].update(event_id=d["audit"]["events"][0]["event_id"]),
    ),
    "approval for a missing case": lambda: mutate(
        "refunds", lambda d: d["approvals"][0].update(case_id="refunds-e-999")
    ),
    "audit_event_ids not in chain": lambda: mutate(
        "refunds", lambda d: case(d, "refunds-e-001")["audit_event_ids"].append("evt-9999")
    ),
    "executed call not recorded": lambda: mutate(
        "refunds", lambda d: d["approvals"][1]["executed"].update(call_id="call-999")
    ),
    "approved with no approver": lambda: mutate(
        "refunds", lambda d: d["approvals"][1].update(approvers=[])
    ),
    "approved with a reject": lambda: mutate(
        "refunds",
        lambda d: d["approvals"][1]["approvers"].append(
            {"role": "x", "decision": "reject", "ts": "2026-11-01T09:00:00Z", "sig_ok": True}
        ),
    ),
    "approved with a bad signature": lambda: mutate(
        "refunds", lambda d: d["approvals"][1]["approvers"][0].update(sig_ok=False)
    ),
    "four eyes with one role twice": lambda: mutate(
        "apexc",
        lambda d: next(a for a in d["approvals"] if a["case_id"] == "apexc-h-002")["approvers"][
            1
        ].update(role="ap_manager"),
    ),
    "four eyes with one person": lambda: mutate(
        "apexc",
        lambda d: [
            p.update(person_ref="p-1")
            for p in next(a for a in d["approvals"] if a["case_id"] == "apexc-h-002")["approvers"]
        ],
    ),
    "rejected without a reject": lambda: mutate(
        "refunds",
        lambda d: next(a for a in d["approvals"] if a["status"] == "rejected")["approvers"][
            0
        ].update(decision="approve"),
    ),
    "ineligible but executed": lambda: mutate(
        "refunds", lambda d: d["approvals"][1].update(eligibility={"ok": False, "reason": "no"})
    ),
    "blocked row executed": lambda: mutate(
        "refunds", lambda d: d["approvals"][1].update(tier="blocked")
    ),
    "approved outcome without a row": lambda: mutate(
        "refunds",
        lambda d: (case(d, "refunds-e-001").update(approval_id=None), d["approvals"].pop(0)),
    ),
    "shadow with applied call": lambda: mutate(
        "askdata",
        lambda d: d["adapter_calls"].append(
            {
                "call_id": "call-900",
                "port": "p",
                "op": "o",
                "idempotency_key": "0" * 64,
                "ts": "2026-11-01T09:00:00Z",
                "outcome": "applied",
                "fault": None,
            }
        ),
    ),
    "auto on nested camelCase money": lambda: mutate(
        "intake", lambda d: d["approvals"][0].update(tier="auto", args={"x": {"AmountMinor": "5"}})
    ),
    "auto on a money tool in other case": lambda: mutate(
        "refunds", lambda d: d["approvals"][0].update(tier="auto", action="Refund-Issue")
    ),
    "integral float seq": lambda: mutate(
        "refunds", lambda d: d["audit"]["events"][0].update(seq=1.0)
    ),
    "bidi character": lambda: mutate(
        "refunds", lambda d: case(d, "refunds-e-001").update(input_summary="pay\u202eevil")
    ),
    "zero width character": lambda: mutate(
        "refunds", lambda d: case(d, "refunds-e-001").update(input_summary="a\u200bb")
    ),
    "impossible date": lambda: mutate(
        "refunds", lambda d: d["run"].update(ts="2026-02-30T09:00:00Z")
    ),
    "trailing newline in a slug": lambda: mutate(
        "refunds", lambda d: d["run"].update(run_id="refunds-example-001\n")
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_internal_truth_rules_reject(name):
    assert problems(CASES[name]()), name


def test_deeply_nested_input_is_refused_not_a_crash(tmp_path):
    d = doc("refunds")
    nested: dict = {}
    cur = nested
    for _ in range(3000):
        cur["a"] = {}
        cur = cur["a"]
    d["approvals"][0]["args"] = nested
    assert any("nested deeper" in p for p in export_rules.check(d))
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert build_console.main([str(deep)]) == 2  # RecursionError is caught, not a traceback
    assert build_console.main([str(tmp_path / "missing.json")]) == 2


def test_all_examples_are_fixtures_with_na_readiness():
    for s in SLUGS:
        d = doc(s)
        assert d["honesty"]["kind"] == "fixture"
        assert {r["status"] for r in d["registry"]["readiness"]} == {"na"}


# ------------------------------------------------------------------------------ 3 evalcore
def test_confident_on_abstain_is_a_hard_zero():
    r = evalcore.CaseRun(
        "x-a-001",
        "abstain",
        True,
        True,
        {"correct": False, "wrong_confident": True, "hard_zero_flags": []},
    )
    sc = evalcore.build_scorecard(
        [r], lock=evalcore.Lock(files={}), lock_sha256="0" * 64, hard_zero_defs=[], split="test"
    )
    assert sc["hard_zeros"]["confident_on_abstain"] == 1 and sc["verdict"] == "not_demonstrated"
    assert sc["wrong_and_allowed"] == {"x": 1, "n": 1}


def test_freeze_refuses_to_overwrite(tmp_path):
    (tmp_path / "g.jsonl").write_text("a", encoding="utf-8")
    evalcore.freeze(tmp_path, ["g.jsonl"])
    with pytest.raises(evalcore.LockError):
        evalcore.freeze(tmp_path, ["g.jsonl"])


def test_record_run_is_exactly_once_and_atomic(tmp_path):
    (tmp_path / "g.jsonl").write_text("a", encoding="utf-8")
    evalcore.freeze(tmp_path, ["g.jsonl"])
    assert evalcore.record_run(tmp_path, "r1", split="dev", status="complete").test_runs == 0
    assert evalcore.record_run(tmp_path, "r1", split="test", status="complete").test_runs == 1
    assert (
        evalcore.record_run(tmp_path, "r1", split="test", status="complete").test_runs == 1
    )  # same run twice
    assert evalcore.record_run(tmp_path, "r2", split=" TEST ", status="aborted").test_runs == 2
    assert not (tmp_path / "lock.json.lock").exists()
    (tmp_path / "lock.json.lock").write_text(
        "", encoding="utf-8"
    )  # a concurrent recorder holds the lock
    with pytest.raises(evalcore.LockError):
        evalcore.record_run(tmp_path, "r3", split="test", status="complete")


@pytest.mark.parametrize("split", ["TEST", "Test", " test "])
def test_sealed_gate_is_not_skipped_by_spelling(split):
    lock = evalcore.Lock(files={}, test_runs=1)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(lock, Decimal(5), split=split, second_look=None)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(lock, Decimal(5), split="sealed", second_look=None)


def test_harness_defect_second_look_is_limited():
    lock = evalcore.Lock(files={}, test_runs=2, harness_defect_looks=1)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(lock, Decimal(5), split="test", second_look="harness_defect")


@pytest.mark.parametrize(
    "bad",
    [
        {"files": {}, "thresholds": {"green_max_upper": "2", "amber_max_upper": "3"}},
        {"files": {}, "thresholds": {"green_max_upper": "0.2", "amber_max_upper": "0.1"}},
        {"files": {}, "thresholds": {"green_max_upper": "NaN", "amber_max_upper": "0.1"}},
        {"files": {}, "thresholds": {"green_max_upper": -0.1, "amber_max_upper": 0.1}},
        {"files": {}, "test_runs": 1.0},
        {"files": {}, "test_runs": True},
        {"files": {}, "test_runs": -1},
        {"files": {}, "second_look_allowed_for": ["anything"]},
        {"files": {"a": "xyz"}},
        {"files": []},
        {"files": {}, "last_test_status": "maybe"},
        {"files": {}, "sealed_min_remaining_usd": "Infinity"},
    ],
)
def test_lock_validation_rejects(bad):
    with pytest.raises(evalcore.LockError):
        evalcore.Lock.from_json(bad)


def test_lock_file_with_nan_is_rejected(tmp_path):
    (tmp_path / "lock.json").write_text('{"files": {}, "test_runs": NaN}', encoding="utf-8")
    with pytest.raises(evalcore.LockError):
        evalcore.read_lock(tmp_path)


# ------------------------------------------------------------------------------ 4 json
@pytest.mark.parametrize(
    "text", ["NaN", "[Infinity]", '{"a": -Infinity}', '{"a": 1e999}', '{"a": 1, "a": 2}']
)
def test_strict_json_rejects(text):
    with pytest.raises(ValueError, match=r"non-finite|out of range|duplicate"):
        strict_json.loads(text)


def test_embed_refuses_nan():
    with pytest.raises(ValueError, match="Out of range|nan|NaN|not JSON"):
        build_console.embed_json({"x": float("nan")})


def test_schema_patterns_have_no_dollar_trap_and_arrays_are_capped():
    text = json.dumps(SCHEMA)
    assert '$"' not in text.replace('"$schema"', "").replace(
        '"$id"', ""
    )  # no pattern ends with a bare $
    assert SCHEMA["$defs"]["case"]["properties"]["steps"]["maxItems"] <= 100
    assert SCHEMA["$defs"]["step"]["properties"]["args_summary"]["maxProperties"] <= 64


# ------------------------------------------------------------------------------ 5, 9 schema additions
def test_comparisons_are_structured_and_formatted():
    d = doc("refunds")
    row = d["approvals"][0]
    assert (
        row["comparisons"][0]["unit"] == "minor_units"
        and row["comparisons"][0]["currency"] == "USD"
    )
    assert "42.00 USD" in json.dumps(row["evidence"])
    assert not re.search(
        r"\b\d{4,} USD\b", json.dumps(row["evidence"])
    )  # no raw minor units beside formatted ones
    row["comparisons"][0]["unit"] = "furlongs"
    assert problems(d)


def test_new_optional_fields_validate_and_are_rejected_when_wrong():
    d = doc("refunds")
    a = d["approvals"][0]
    assert a["requested_by"] == "agent" and any("required_roles" in x for x in d["approvals"])
    a["expires_at"] = "2026-11-02T09:00:00Z"
    a["approvers"][0]["person_ref"] = "person-17"
    assert not problems(d)
    a["requested_by"] = "Mallory"
    assert problems(d)
    d = doc("refunds")
    assert any("model_saw" in s for c in d["cases"] for s in c["steps"])
    assert any(c.get("facts") for c in d["cases"])
    c = case(d, "refunds-h-003")
    c["reconcile"]["call_id"] = "call-001"
    assert not [e for e in V.iter_errors(d)]


# ------------------------------------------------------------------------------ 12 hosting and CSP
def inline_parts(page):
    style = re.search(r"<style>(.*?)</style>", page, re.DOTALL).group(1)
    scripts = re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", page, re.DOTALL)
    return style, scripts


def b64(text):
    import base64
    import hashlib

    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.needs_samples
def test_csp_hashes_are_recomputed_from_the_built_bytes(slug, tmp_path):
    out = tmp_path / "index.html"
    assert (
        build_console.main([str(KIT / "demo_only" / "examples" / f"{slug}.json"), "-o", str(out)])
        == 0
    )
    page = out.read_text(encoding="utf-8")
    style, scripts = inline_parts(page)
    assert len(scripts) == 1
    meta = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert b64(scripts[0]) in meta and b64(style) in meta
    headers = (tmp_path / "_headers").read_text(encoding="utf-8")
    csp = re.search(r"Content-Security-Policy: (.+)", headers).group(1)
    assert csp == meta + "; frame-ancestors 'none'"  # the two never drift
    for want in (
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "require-trusted-types-for 'script'",
        "default-src 'none'",
    ):
        assert want in csp
    assert "img-src" not in csp and "unsafe" not in csp
    for h in (
        "X-Content-Type-Options: nosniff",
        "Referrer-Policy: no-referrer",
        "X-Frame-Options: DENY",
        "Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy: same-origin",
        "Cross-Origin-Resource-Policy: same-origin",
        "Strict-Transport-Security: max-age=31536000",
        "Cache-Control: no-cache",
        "X-Robots-Tag: noindex",
    ):
        assert h in headers


def test_hosting_notes_name_the_cloudflare_settings():
    text = (KIT / "HOSTING.md").read_text(encoding="utf-8")
    for want in ("Web Analytics", "Rocket Loader", "Email Address Obfuscation", "Auto Minify"):
        assert want in text


def test_sources_use_no_storage_or_history_api():
    src = "".join((KIT / "console" / n).read_text(encoding="utf-8") for n in ("app.js", "chain.js"))
    for bad in (
        "document.cookie",
        "indexedDB",
        "caches.",
        "serviceWorker",
        "pushState",
        "replaceState",
        "localStorage",
        "sessionStorage",
        "innerHTML",
        "eval(",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "sendBeacon",
    ):
        assert bad not in src, bad


@pytest.mark.parametrize("n", [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000])
def test_js_sha256_matches_at_padding_boundaries(n):
    node = require("node")
    script = (
        "const m=require(process.argv[1]);const c=require('crypto');const t='a'.repeat(+process.argv[2]);"
        "if(m.sha256(t)!==c.createHash('sha256').update(t).digest('hex'))process.exit(3);"
    )
    res = subprocess.run(
        [node, "-e", script, str(KIT / "console" / "chain.js"), str(n)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr


# ------------------------------------------------------------------------------ 13 harness
def test_plugin_blocks_the_other_network_doors(tmp_path):
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 80)
    for name, args in (
        ("gethostbyaddr", ("127.0.0.1",)),
        ("getnameinfo", (("127.0.0.1", 80), 0)),
        ("getfqdn", ()),
        ("gethostbyname_ex", ("example.com",)),
    ):
        with pytest.raises(NetworkBlocked):
            getattr(socket, name)(*args)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, pytest.raises(NetworkBlocked):
        udp.sendto(b"x", ("127.0.0.1", 9))
    if socket.has_ipv6:
        with (
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s6,
            pytest.raises(NetworkBlocked),
        ):
            s6.connect(("::1", 9))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        with pytest.raises(NetworkBlocked):
            srv.bind(("127.0.0.1", 0))
        with pytest.raises(NetworkBlocked):
            srv.listen()
    if hasattr(socket, "AF_UNIX"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as u, pytest.raises(NetworkBlocked):
            u.bind(str(tmp_path / "s.sock"))
    if hasattr(socket.socket, "sendmsg"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(NetworkBlocked):
            s.sendmsg([b"x"], [], 0, ("127.0.0.1", 9))
    if hasattr(socket, "fromfd"):
        with pytest.raises(NetworkBlocked):
            socket.fromfd(0, socket.AF_INET, socket.SOCK_STREAM)


def test_plugin_blocks_asyncio_connections_but_the_loop_itself_runs():
    async def go():
        await asyncio.open_connection("127.0.0.1", 9)

    async def idle():
        await asyncio.sleep(0)
        return 1

    assert asyncio.run(idle()) == 1  # the event loop uses socketpair, which stays allowed
    with pytest.raises((NetworkBlocked, OSError)):
        asyncio.run(go())


def test_plugin_blocks_network_tools():
    with pytest.raises(NetworkBlocked):
        subprocess.run(["curl", "http://example.com"], check=False)
    with pytest.raises(NetworkBlocked):
        subprocess.Popen("wget http://example.com", shell=True)


@pytest.fixture(scope="session")
def _session_fixture_sees_the_block():
    return socket.socket.connect


def test_session_fixtures_are_covered(_session_fixture_sees_the_block):
    with pytest.raises(NetworkBlocked):
        _session_fixture_sees_the_block(None, ("127.0.0.1", 9))


def _script(tmp_path, case_id="refunds-e-001", turn=1, raw=None, **extra):
    d = tmp_path / case_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"turn-{turn}.json").write_text(
        raw if raw is not None else json.dumps({"text": "ok", **extra}), encoding="utf-8"
    )


def test_scripted_llm_is_strict(tmp_path):
    msgs = [{"role": "user", "content": "hi"}]
    _script(tmp_path, prompt_sha256=prompt_sha256("s", msgs))
    llm = ScriptedLLM(tmp_path)
    assert llm.reply("refunds-e-001", 1, system="s", messages=msgs).model == "scripted"
    for bad_turn in (0, -1, 1.0, True, "1", None):
        with pytest.raises(ValueError, match="turn"):
            llm.reply("refunds-e-001", bad_turn, system="s", messages=msgs)
    with pytest.raises(ValueError, match="case_id"):
        llm.reply(
            "refunds-e-001\n", 1, system="s", messages=msgs
        )  # trailing newline passed the old $ regex
    assert not issubclass(MissingScript, KeyError)
    with pytest.raises(MissingScript):
        llm.reply("refunds-e-001", 2, system="s", messages=msgs)
    _script(tmp_path, case_id="refunds-e-002", raw=json.dumps({"text": "no hash"}))
    with pytest.raises(PromptDrift, match="no prompt_sha256"):
        llm.reply("refunds-e-002", 1, system="s", messages=msgs)
    assert (
        ScriptedLLM(tmp_path, allow_unhashed=True)
        .reply("refunds-e-002", 1, system="s", messages=msgs)
        .text
        == "no hash"
    )
    for i, raw in enumerate(['{"text": "a", "text": "b"}', '{"text": NaN}', "[1, 2]", "null"]):
        _script(tmp_path, case_id=f"refunds-e-1{i:02d}", raw=raw)
        with pytest.raises(ValueError, match="duplicate|non-finite|object"):
            ScriptedLLM(tmp_path, allow_unhashed=True).reply(
                f"refunds-e-1{i:02d}", 1, system="s", messages=msgs
            )


# ------------------------------------------------------------------------------ browser tests (fail, not skip, in CI)
def find_browser():
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "chrome",
    ):
        if shutil.which(name):
            return shutil.which(name)
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).is_file():
            return p
    return None


def dump(page, route):
    browser = find_browser()
    if browser is None:
        require("a-chromium-family-browser")
    url = Path(page).resolve().as_uri() + "#/" + route
    res = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--virtual-time-budget=3000",
            "--dump-dom",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    return res.stdout


def page_for(tmp_path, d, name="p", rules=True):
    f = tmp_path / f"{name}.html"
    f.write_text(build_console.build(d, rules=rules), encoding="utf-8")
    return f


@pytest.mark.browser
def test_verify_button_reports_internally_consistent(tmp_path):
    f = page_for(tmp_path, doc("refunds"))
    dom = dump(f, "run?do=verify&theme=light")
    assert "internally consistent" in dom and "agrees with the export" in dom
    assert "event text fields only" in dom
    assert 'data-js-errors="0"' in dom and 'data-csp-violations="0"' in dom


@pytest.mark.browser
def test_disagreeing_claim_is_shown_as_bad_by_default(tmp_path):
    d = doc("refunds")
    d["audit"]["events"][5]["summary"] += (
        " (edited after sealing)"  # claimed verify block still says ok
    )
    f = page_for(tmp_path, d, rules=False)  # a hand-made page: the build normally refuses this
    dom = dump(f, "approvals")
    assert (
        "The audit chain does not verify" in dom
        and "Export claims 56 of 56" in dom
        and "recomputed in this page: 55 of 56" in dom
    )
    dom = dump(f, "run?do=verify")
    assert "DISAGREES" in dom and "mismatch at event 6" in dom


@pytest.mark.browser
def test_sample_only_buttons_write_nothing(tmp_path):
    f = page_for(tmp_path, doc("refunds"))
    for act in ("approve", "reject"):
        dom = dump(f, f"approvals?do={act}")
        assert f"Sample only: the {act} button writes nothing" in dom
    dom = dump(page_for(tmp_path, doc("apexc"), "ap"), "approvals?do=approve")
    assert "Blocked, cannot be approved" in dom


@pytest.mark.browser
def test_scripted_export_never_shows_green_and_testdouble_does(tmp_path):
    d = doc("askdata")
    d["scorecard"]["verdict"] = "green"  # hand-made, bypassing the build rules
    dom = dump(page_for(tmp_path, d, "g", rules=False), "scorecard")
    assert (
        "Plumbing check only, no accuracy claim" in dom
        and 'class="verdict verdict-green"' not in dom
        and 'class="verdict verdict-amber"' in dom
    )
    assert "capped below green" in dom
    fx = strict_json.load_object(KIT / "demo_only" / "fixtures" / "green-testdouble.json")
    dom = dump(page_for(tmp_path, fx, "t"), "scorecard")
    assert (
        'class="verdict verdict-green"' in dom
        and "Demonstrated (smoke)" in dom
        and "FIXTURE, not a run" in dom
    )


@pytest.mark.browser
def test_hostile_names_and_malformed_urls_render_as_plain_data(tmp_path):
    d = doc("refunds")
    d["approvals"][0]["action"] = "constructor"
    d["registry"]["agent_id"] = "constructor"
    d["scorecard"]["hard_zero_defs"].append({"key": "constructor", "meaning": "a hostile key"})
    d["scorecard"]["hard_zeros"]["constructor"] = 0
    d["audit"]["events"][0]["event_id"] = "constructor"
    d["run"]["model"] = "scripted"
    d["registry"]["tools"][0]["name"] = "__proto__"
    for c in d["cases"]:
        c["audit_event_ids"] = [
            ("constructor" if e == "evt-0001" else e) for e in c["audit_event_ids"]
        ]
    reseal(d)
    f = page_for(tmp_path, d, "h")
    for route in (
        "run?case=constructor",
        "__proto__",
        "run?case=%",
        "run?case=%E0%A4%A&theme=%",
        "scorecard",
        "registry",
        "approvals",
    ):
        dom = dump(f, route)
        assert 'data-js-errors="0"' in dom, route
        assert "[native code]" not in dom and "function Object" not in dom, route
    assert "data-rendered=" in dom


@pytest.mark.browser
def test_empty_export_shows_a_demo_aware_message(tmp_path):
    d = doc("dealdesk")
    d.update(cases=[], approvals=[], adapter_calls=[])
    d["audit"].update(
        events=[],
        verify={
            "events": 0,
            "matched": 0,
            "ok": True,
            "text": "local integrity check: 0 of 0 events match",
        },
    )
    d["scorecard"].update(
        n=0,
        n_answered=0,
        n_refused=0,
        pass_rate=0.0,
        pass_k=None,
        hard_zeros={k: 0 for k in d["scorecard"]["hard_zeros"]},
        verdict="not_demonstrated",
    )
    d["scorecard"]["confident_wrong"].update(
        x=0, n_answered=0, rate=None, upper_bound=None, items=[]
    )
    d["scorecard"]["false_abstain"].update(x=0, n_should_answer=0, rate=None)
    d["scorecard"]["wrong_and_allowed"].update(x=0, n=0)
    d["scorecard"]["coverage"].update(answered_correct=0, definite=0)
    d["scorecard"]["abstention"].update(precision=None, recall=None)
    f = page_for(tmp_path, d, "e", rules=False)
    assert "no cases for" in dump(f, "approvals").lower() or "Nothing matches" in dump(
        f, "approvals"
    )
    assert 'data-js-errors="0"' in dump(f, "run")
