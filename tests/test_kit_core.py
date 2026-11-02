import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from hadavin_kit import chain, evalcore
from hadavin_kit.pytest_plugin import NetworkBlocked
from hadavin_kit.scripted_llm import (
    MissingScript,
    PromptDrift,
    ScriptedLLM,
    prompt_sha256,
)
from jsonschema import Draft202012Validator

KIT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8"))
EXAMPLES = sorted((KIT / "demo_only" / "examples").glob("*.json")) or [
    KIT
    / "demo_only"
    / "examples"
    / "missing.json"  # cut test: loading it skips the test (see tests/conftest.py)
]
SLUGS = ["apexc", "askdata", "dealdesk", "intake", "refunds"]


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_schema_is_valid_and_strict():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["additionalProperties"] is False


def test_five_examples_exist():
    if not (KIT / "demo_only").is_dir():
        pytest.skip("needs the sample-data folder (cut test)")
    assert [p.stem for p in EXAMPLES] == SLUGS


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_validates_and_chain_verifies(path):
    doc = load(path)
    errs = list(Draft202012Validator(SCHEMA).iter_errors(doc))
    assert not errs, [e.message for e in errs[:3]]
    v = chain.verify(doc["audit"]["events"], doc["audit"]["chain"])
    assert v == doc["audit"]["verify"] and v["ok"]
    assert doc["audit"]["verify"]["text"].startswith("local integrity check:")


def test_unknown_key_is_rejected():
    doc = load(EXAMPLES[0])
    doc["extra"] = 1
    assert list(Draft202012Validator(SCHEMA).iter_errors(doc))
    doc = load(EXAMPLES[0])
    doc["scorecard"]["surprise"] = 1
    assert list(Draft202012Validator(SCHEMA).iter_errors(doc))


def test_examples_cover_required_situations():
    docs = {p.stem: load(p) for p in EXAMPLES}
    r = docs["refunds"]
    assert any(s["decision"] == "deny" for c in r["cases"] for s in c["steps"])  # a denied action
    assert any(a["status"] == "rejected" for a in r["approvals"])
    assert any(a["status"] == "pending" for a in r["approvals"])
    assert any(a["executed"] and a["executed"]["result"] == "unknown" for a in r["approvals"])
    assert any(c["outcome"] == "unknown" for c in r["cases"])
    verdicts = {s: d["scorecard"]["verdict"] for s, d in docs.items()}
    assert "not_demonstrated" in verdicts.values()
    assert verdicts["intake"] == "amber" and verdicts["askdata"] == "amber"
    for d in docs.values():
        sc = d["scorecard"]
        assert "false_abstain" in sc and "wrong_and_allowed" in sc
        assert d["run"]["model"] == "scripted" and d["honesty"]["adapters"] == "fake"


def test_replay_gives_one_applied_two_duplicates():
    r = load(KIT / "demo_only" / "examples" / "refunds.json")
    calls = [
        c
        for c in r["adapter_calls"]
        if c["idempotency_key"]
        == r["approvals"][
            next(i for i, a in enumerate(r["approvals"]) if a["case_id"] == "refunds-h-004")
        ]["idempotency_key"]
    ]
    assert [c["outcome"] for c in calls] == ["applied", "duplicate_ignored", "duplicate_ignored"]


def test_chain_detects_tamper_and_reorder():
    audit = load(EXAMPLES[0])["audit"]
    events, cid = audit["events"], audit["chain"]
    assert chain.verify(events, cid)["ok"]
    bad = json.loads(json.dumps(events))
    bad[3]["summary"] += "!"
    v = chain.verify(bad, cid)
    assert not v["ok"] and v["matched"] < v["events"]
    swapped = json.loads(json.dumps(events))
    swapped[1], swapped[2] = swapped[2], swapped[1]
    assert not chain.verify(swapped, cid)["ok"]


def test_chain_hash_is_ascii_escaped_for_unicode():
    w = chain.ChainWriter()
    ev = w.append(
        ts="2026-11-01T09:00:00Z", type="intent", actor="agent", case_id="", summary="Zoë 🙂 \x7f"
    )
    assert chain.verify(w.events)["ok"] and len(ev["sha256"]) == 64
    with pytest.raises(ValueError):
        w.append(ts="2026-11-01T09:00:00Z", type="nope", actor="agent", case_id="", summary="x")


# ---------------------------------------------------------------------------------------------- ScriptedLLM
def _script(tmp_path, case_id="refunds-e-001", turn=1, **extra):
    d = tmp_path / case_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"turn-{turn}.json").write_text(
        json.dumps({"text": "ok", "input_tokens": 5, **extra}), encoding="utf-8"
    )


def test_scripted_reply_and_missing_script(tmp_path):
    _script(tmp_path)
    llm = ScriptedLLM(tmp_path, allow_unhashed=True)
    r = llm.reply("refunds-e-001", 1, system="s", messages=[{"role": "user", "content": "hi"}])
    assert r.model == "scripted" and r.text == "ok" and r.cost_usd == "0"
    with pytest.raises(MissingScript):
        llm.reply("refunds-e-001", 2, system="s", messages=[])
    with pytest.raises(MissingScript):
        llm.reply("refunds-e-002", 1, system="s", messages=[])
    with pytest.raises(ValueError):
        llm.reply("../x", 1, system="s", messages=[])


def test_prompt_drift_is_detected(tmp_path):
    msgs = [{"role": "user", "content": "hi"}]
    _script(tmp_path, prompt_sha256=prompt_sha256("sys", msgs))
    llm = ScriptedLLM(tmp_path)
    assert llm.reply("refunds-e-001", 1, system="sys", messages=msgs).prompt_sha256
    with pytest.raises(PromptDrift):
        llm.reply("refunds-e-001", 1, system="sys v2", messages=msgs)


# ---------------------------------------------------------------------------------------------- sockets
def test_socket_use_fails():
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.com", 80))
    with pytest.raises(NetworkBlocked), socket.socket() as s:
        s.connect(("127.0.0.1", 9))
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 80)


# ---------------------------------------------------------------------------------------------- evalcore
def run(cid, expected, answered, allowed, correct, wrong=False, flags=()):
    return evalcore.CaseRun(
        cid, expected, answered, allowed,
        {"correct": correct, "wrong_confident": wrong, "hard_zero_flags": list(flags)},
        Decimal("0.001"), 100,
    )  # fmt: skip


DEFS = [{"key": "hz", "meaning": "a hard zero"}]


def test_scorecard_metrics_and_mandatory_blocks():
    runs = [run(f"x-e-{i:03d}", "act", True, True, True) for i in range(20)]
    runs += [
        run("x-a-001", "abstain", False, False, True),
        run("x-e-100", "act", False, False, False),
    ]
    sc = evalcore.build_scorecard(
        runs, lock=evalcore.Lock(files={}), lock_sha256="0" * 64, hard_zero_defs=DEFS, split="test"
    )
    assert sc["verdict"] == "amber" and sc["verdict_text"].startswith("promising, not demonstrated")
    assert sc["false_abstain"] == {"x": 1, "n_should_answer": 21, "rate": 1 / 21}
    assert sc["wrong_and_allowed"] == {"x": 0, "n": 20}
    assert sc["confident_wrong"]["x"] == 0 and sc["abstention"]["recall"] == 1.0
    assert sc["hard_zeros"] == {"hz": 0, "confident_on_abstain": 0}


def test_hard_zero_forces_not_demonstrated_and_wrong_and_allowed_counts():
    runs = [run(f"x-e-{i:03d}", "act", True, True, True) for i in range(40)]
    runs.append(run("x-t-001", "refuse", True, True, False, flags=["hz"]))
    sc = evalcore.build_scorecard(
        runs, lock=evalcore.Lock(files={}), lock_sha256="0" * 64, hard_zero_defs=DEFS, split="test"
    )
    assert sc["verdict"] == "not_demonstrated"
    assert sc["wrong_and_allowed"]["x"] == 1
    with pytest.raises(ValueError):
        evalcore.build_scorecard(
            [run("a-e-001", "act", True, True, True, flags=["nope"])],
            lock=evalcore.Lock(files={}),
            lock_sha256="0" * 64,
            hard_zero_defs=DEFS,
            split="dev",
        )


def test_verdict_bands():
    th = evalcore.Thresholds()
    assert evalcore.verdict_for(0, 30, 0, th)[0] == "green"
    assert evalcore.verdict_for(0, 20, 0, th)[0] == "amber"
    assert evalcore.verdict_for(0, 5, 0, th)[0] == "not_demonstrated"
    assert evalcore.verdict_for(0, 0, 0, th) == ("not_demonstrated", None)
    assert evalcore.verdict_for(0, 300, 1, th)[0] == "not_demonstrated"


def test_helpers():
    assert evalcore.percentile([], 0.5) == 0 and evalcore.percentile([5, 1, 9], 0.5) == 5
    ids = [f"i{n}" for n in range(10)]
    assert evalcore.human_read_sample(ids) == evalcore.human_read_sample(list(reversed(ids)))
    assert len(evalcore.human_read_sample(ids)) == 2


def test_lock_freeze_verify_and_sealed_gate(tmp_path):
    (tmp_path / "gold.jsonl").write_text("a\n", encoding="utf-8")
    lock = evalcore.freeze(tmp_path, ["gold.jsonl"])
    assert evalcore.read_lock(tmp_path) == lock and evalcore.verify_files(tmp_path, lock) == []
    (tmp_path / "gold.jsonl").write_text("b\n", encoding="utf-8")
    assert evalcore.verify_files(tmp_path, lock) == ["gold.jsonl"]
    evalcore.sealed_gate(lock, Decimal(5), split="test", second_look=None)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(lock, Decimal(1), split="test", second_look=None)
    used = evalcore.Lock(files={}, test_runs=1)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(used, Decimal(5), split="test", second_look=None)
    with pytest.raises(evalcore.LockError):
        evalcore.sealed_gate(used, Decimal(5), split="test", second_look="aborted")
    evalcore.sealed_gate(used, Decimal(5), split="test", second_look="harness_defect")
    evalcore.sealed_gate(used, Decimal(0), split="dev", second_look=None)
    (tmp_path / "lock.json").write_text('{"files": {}, "bogus": 1}', encoding="utf-8")
    with pytest.raises(evalcore.LockError):
        evalcore.read_lock(tmp_path)


def test_compare_and_report(tmp_path):
    good = load(KIT / "demo_only" / "examples" / "intake.json")["scorecard"]
    worse = json.loads(json.dumps(good))
    worse["false_abstain"]["x"] += 1
    assert evalcore.compare_scorecards(good, worse)["regression"] is True
    assert evalcore.compare_scorecards(good, good)["regression"] is False
    other = json.loads(json.dumps(good))
    other["lock_sha256"] = "1" * 64
    with pytest.raises(evalcore.Incomparable):
        evalcore.compare_scorecards(good, other)
    evalcore.write_report(tmp_path, "demo", good)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "False abstain" in md and "Wrong and allowed" in md and "smoke" in md


def test_scripted_scorecard_is_capped_below_green():
    runs = [run(f"x-e-{i:03d}", "act", True, True, True) for i in range(35)]
    kw = {
        "lock": evalcore.Lock(files={}),
        "lock_sha256": "0" * 64,
        "hard_zero_defs": DEFS,
        "split": "test",
    }
    assert evalcore.build_scorecard(runs, scripted=False, **kw)["verdict"] == "green"
    sc = evalcore.build_scorecard(runs, **kw)
    assert sc["scripted"] is True and sc["verdict"] == "amber" and sc["n_answered"] == 35
