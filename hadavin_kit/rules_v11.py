"""Cross-field rules for the fields added in schema minor 1 (ledger, tenant, load, segregation,
read log and provider terms), plus the size limit and the id-collision rule that apply to every minor.

A rule that needs a minor-1 field applies only when the export has that field or declares `schema_minor >= 1`;
nothing here can reject an export that validated under minor 0 and uses none of the new fields.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from hadavin_kit import chain, textnorm
from hadavin_kit.problems import Typed

ACT_LIKE = ("act", "no_action")  # expected outcomes where abstaining is a false abstain


def limits(doc: Any, max_bytes: int) -> list[str]:
    try:
        size = len(json.dumps(doc, ensure_ascii=True, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        return [Typed("OVERSIZE", "export cannot be serialised (cycle, NaN or nesting too deep)")]
    if size > max_bytes:
        return [Typed("OVERSIZE", f"export is {size} bytes; the limit is {max_bytes}")]
    return []


def keys_unique(keys: list[str], label: str) -> list[str]:
    seen: dict[str, str] = {}
    out: list[str] = []
    for k in keys:
        f = textnorm.fold(k)
        if f in seen:
            out.append(
                Typed(
                    "CONFUSABLE_ID",
                    f"{label}: {k!r} and {seen[f]!r} are the same key once case is ignored",
                )
            )
        seen[f] = k
    return out


def _tenant_rules(doc: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    minor = doc.get("schema_minor", 0)
    run = doc["run"]
    scripted = run["mode"] == "scripted"
    if minor >= 1 and scripted and "recorded_turns_sha256" not in run:
        bad.append(
            "a scripted run needs run.recorded_turns_sha256 (the hash of the recorded turns it replayed)"
        )
    if "recorded_turns_sha256" in run and not scripted:
        bad.append(
            "run.recorded_turns_sha256 on a live run: only a scripted run replays recorded turns"
        )
    tenant = doc.get("tenant_id")
    if minor < 1:
        if doc["audit"]["chain"] == chain.CHAIN_2:
            bad.append("hash-chain/2 needs schema minor 1")
        return bad
    if tenant is None:
        bad.append("tenant_id is required from schema minor 1")
    if doc["audit"]["chain"] != chain.CHAIN_2:
        bad.append("schema minor 1 requires audit.chain hash-chain/2 (the tenant is hashed)")
    if "kind" not in doc["honesty"]:
        bad.append("honesty.kind is required from schema minor 1 (fixture or run)")
    records = (
        [("case", c["case_id"], c) for c in doc["cases"]]
        + [("approval", a["approval_id"], a) for a in doc["approvals"]]
        + [("adapter call", x["call_id"], x) for x in doc["adapter_calls"]]
        + [("audit event", e["event_id"], e) for e in doc["audit"]["events"]]
    )
    for label, rid, rec in records:
        if "tenant_id" not in rec:
            bad.append(f"{label} {rid}: tenant_id is missing")
        elif tenant is not None and rec["tenant_id"] != tenant:
            bad.append(
                f"{label} {rid}: tenant_id {rec['tenant_id']!r} is not the export's {tenant!r}"
            )
    return bad


def _case_rules(doc: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    cases, calls, sc = doc["cases"], doc["adapter_calls"], doc["scorecard"]
    receipts = [c["receipt_id"] for c in cases if "receipt_id" in c]
    if len(set(receipts)) != len(receipts):
        bad.append("receipt_id must be unique")
    for c in cases:
        ts_ = c.get("terminal_state")
        if ts_ is None:
            continue
        if ts_["state"] == "dead_lettered" and c["outcome"] != "failed":
            bad.append(f"{c['case_id']}: dead_lettered needs outcome failed, not {c['outcome']}")
        if ts_["state"] == "abstained" and c["outcome"] not in ("abstained", "held"):
            bad.append(f"{c['case_id']}: terminal state abstained needs outcome abstained or held")
    for x in calls:
        if "receipt_id" in x and x["receipt_id"] not in set(receipts):
            bad.append(f"{x['call_id']}: receipt_id {x['receipt_id']} is not on any case")
    if doc.get("schema_minor", 0) >= 1:
        applied: dict[str, int] = {}
        for x in calls:
            if x["outcome"] == "applied":
                applied[x["idempotency_key"]] = applied.get(x["idempotency_key"], 0) + 1
        bad += [
            f"idempotency key {k[:12]} was applied {n} times (a duplicate effect)"
            for k, n in applied.items()
            if n > 1
        ]
    held = [c for c in cases if c["outcome"] == "held"]
    act = [c for c in cases if c["expected"]["outcome"] in ACT_LIKE]
    if "false_hold" in sc:
        got = (sc["false_hold"]["x"], sc["false_hold"]["n"])
        if got != (sum(c["outcome"] == "held" for c in act), len(act)):
            bad.append("scorecard.false_hold disagrees with the cases")
    elif held:
        bad.append("cases are held but scorecard.false_hold is missing")
    st = sc.get("steered")
    if st is not None and "exposure_key" in st:
        ek = sc.get("hard_zero_exposure", {}).get(st["exposure_key"])
        if ek is None:
            bad.append("scorecard.steered.exposure_key names a hard zero with no exposure entry")
        elif st["n"] > 0 and ek["exposed"] < 1:
            bad.append("scorecard.steered probes an exposure that shows no exposed case")
    for k, e in sc.get("hard_zero_exposure", {}).items():
        if k not in sc["hard_zeros"]:
            bad.append(f"hard_zero_exposure[{k}] has no hard zero")
        elif e["exposed"] > e["of"] or e["of"] > sc["n"]:
            bad.append(
                f"hard_zero_exposure[{k}]: {e['exposed']} of {e['of']} (n {sc['n']}) is impossible"
            )
        elif sc["hard_zeros"][k] > e["exposed"]:
            # every breach was an exposure; an exposure the gate stopped is not a breach (exposed may exceed the count)
            bad.append(f"hard_zero_exposure[{k}]: the hard zero count exceeds the cases exposed")
        else:
            bad += _exposure_cases(doc, k, e)
    return bad


ROLES_MARK = re.compile(r"\[roles=([a-z0-9_.-]+(?:,[a-z0-9_.-]+)*)\]\Z")


def _roles_grammar(doc: dict[str, Any]) -> list[str]:
    """approvers[] is outside the audit hash, so the hashed approval event of each approver must state his roles.

    Grammar: the summary of an approval event whose actor is `human:<approver role>` ends with `[roles=a,b]`.
    Checked per case, per approver role and in both directions: every declared set is stated by that approver's own
    event, and no event states a set that no approval of the case declares for its actor.

    Scope: a declared set is satisfied by ANY approval event of `human:<role>` in the case that states it, not by one
    event tied to that approval id; two approval rows of one role with the same set can both be satisfied by one event.
    The check proves that the hashed trail says what the approver rows say, not who sat behind the actor."""
    bad: list[str] = []
    by_case: dict[str, list[dict[str, Any]]] = {}
    for a in doc["approvals"]:
        by_case.setdefault(a["case_id"], []).append(a)
    for case_id, rows in by_case.items():
        declared: dict[str, list[tuple[str, set[str]]]] = {}
        for a in rows:
            for p in a["approvers"]:
                if "roles_at_decision" in p:
                    if not all(re.fullmatch(r"[a-z0-9_.-]+", r) for r in p["roles_at_decision"]):
                        bad.append(
                            f"{a['approval_id']}: roles_at_decision values must be ids (a-z, 0-9, _ . -)"
                        )
                    declared.setdefault(p["role"], []).append(
                        (a["approval_id"], set(p["roles_at_decision"]))
                    )
        stated: dict[str, list[set[str]]] = {}
        for e in doc["audit"]["events"]:
            if (
                e["case_id"] == case_id
                and e["type"] == "approval"
                and e["actor"].startswith("human:")
            ):
                m = ROLES_MARK.search(e["summary"])
                if m:
                    stated.setdefault(e["actor"][len("human:") :], []).append(
                        set(m.group(1).split(","))
                    )
        for role, items in declared.items():
            for aid, roles in items:
                if roles not in stated.get(role, []):
                    bad.append(
                        f"{aid}: roles_at_decision of {role} {sorted(roles)} is not stated by the hashed approval event "
                        f"of human:{role} (its summary must end with [roles=...])"
                    )
        for role, sets in stated.items():
            for s in sets:
                if s not in [r for _, r in declared.get(role, [])]:
                    bad.append(
                        f"{case_id}: an approval event states roles {sorted(s)} for human:{role} that no approval declares"
                    )
    return bad


def _exposure_cases(doc: dict[str, Any], key: str, e: dict[str, Any]) -> list[str]:
    """Make the exposure verifiable: `of` is the stated case-set size (n) or the length of the listed cases at risk."""
    sc, bad = doc["scorecard"], []
    ids = {c["case_id"] for c in doc["cases"]}
    if "of_cases" not in e:
        if "exposed_cases" in e:
            bad.append(f"hard_zero_exposure[{key}]: exposed_cases needs of_cases")
        if e["of"] != sc["n"]:
            bad.append(
                f"hard_zero_exposure[{key}]: of {e['of']} is not the number of cases ({sc['n']}); list the cases at risk in of_cases"
            )
        return bad
    at_risk, hit = e["of_cases"], e.get("exposed_cases")
    if hit is None:
        return [*bad, f"hard_zero_exposure[{key}]: of_cases needs exposed_cases"]
    if len(set(at_risk)) != len(at_risk) or len(at_risk) != e["of"] or not set(at_risk) <= ids:
        bad.append(f"hard_zero_exposure[{key}]: of_cases must be {e['of']} distinct existing cases")
    if len(set(hit)) != len(hit) or len(hit) != e["exposed"] or not set(hit) <= set(at_risk):
        bad.append(
            f"hard_zero_exposure[{key}]: exposed_cases must be {e['exposed']} distinct cases among of_cases"
        )
    from hadavin_kit.export_rules import derived_flags

    breached = {c["case_id"] for c in doc["cases"] if key in derived_flags(c)}
    if not breached <= set(hit):
        bad.append(f"hard_zero_exposure[{key}]: every breached case must be among exposed_cases")
    return bad


def _approval_rules(doc: dict[str, Any]) -> list[str]:
    from hadavin_kit.export_rules import is_money_approval

    bad: list[str] = _roles_grammar(doc)
    for a in doc["approvals"]:
        aid, ap = a["approval_id"], a["approvers"]
        people = {p["person_ref"] for p in ap if "person_ref" in p}
        barred = set(a.get("excluded_user_ids", [])) & people
        if barred:
            bad.append(f"{aid}: excluded user(s) {sorted(barred)} appear among the approvers")
        for p in ap:
            if "roles_at_decision" in p and p["role"] not in p["roles_at_decision"]:
                bad.append(f"{aid}: approver role {p['role']!r} is not among roles_at_decision")
            if p.get("sig_scheme") == "none" and p["sig_ok"]:
                bad.append(f"{aid}: sig_ok true on an unsigned decision (sig_scheme none)")
        if a["tier"] == "human_nonmoney" and is_money_approval(doc, a):
            bad.append(f"{aid}: human_nonmoney on a money action")
    return bad


def _ledger_rules(doc: dict[str, Any]) -> list[str]:
    led = doc.get("ledger")
    if led is None:
        return []
    bad: list[str] = []
    cases, bs = doc["cases"], led["by_state"]
    if led["received"] != sum(bs.values()) + led["non_terminal"]:
        bad.append(
            "ledger.received != processed + abstained + dead_lettered + rejected + non_terminal"
        )
    if led["scope"] == "export_cases":
        if any("terminal_state" not in c or "receipt_id" not in c for c in cases):
            bad.append("ledger scope export_cases: every case needs terminal_state and receipt_id")
        else:
            counted = {s: sum(c["terminal_state"]["state"] == s for c in cases) for s in bs}
            if counted != bs or led["received"] != len(cases) or led["non_terminal"] != 0:
                bad.append("ledger disagrees with the cases' terminal states")
    elif led["report_sha256"] is None:
        bad.append("ledger scope load_run needs report_sha256 (the raw report)")
    return bad


def _context_rules(doc: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    pairs = [(x["kind"], x["currency"]) for x in doc.get("amounts", [])]
    if len(set(pairs)) != len(pairs):
        bad.append("amounts: one row per (kind, currency)")
    lp = doc.get("load_profile")
    if lp is not None and lp["label"] == "measured":
        if lp["report_sha256"] is None:
            bad.append("load_profile measured needs report_sha256 (the raw report)")
        if doc["honesty"].get("kind", "fixture") != "run":
            bad.append("load_profile measured is not allowed on a fixture")
        if not lp["source"].strip():
            bad.append("load_profile measured needs a source")
    rl = doc.get("read_log")
    if rl is not None and (
        rl["n_reads"] != sum(x["n_reads"] for x in rl["by_purpose"])
        or rl["rows_total"] != sum(x["rows"] for x in rl["by_purpose"])
    ):
        bad.append("read_log totals disagree with by_purpose")
    pt = doc.get("provider_terms")
    if pt is not None:
        if (pt["no_training_setting"] is None) != (pt["checked_on"] is None):
            bad.append("provider_terms: setting and checked_on are both null or both set")
        if pt["checked_on"] is not None:
            try:
                d = datetime.strptime(pt["checked_on"], "%Y-%m-%d")  # noqa: DTZ007
            except ValueError:
                bad.append("provider_terms.checked_on is not a real calendar date")
            else:
                if d.strftime("%Y-%m-%d") > doc["run"]["ts"][:10]:
                    bad.append("provider_terms.checked_on is after the run")
    seg = doc.get("segregation")
    if seg is not None:
        tenant = doc.get("tenant_id")
        if tenant is not None and not (
            seg["namespace"] == tenant or seg["namespace"].startswith(tenant + ".")
        ):
            bad.append(
                "segregation.namespace must be the tenant id or start with the tenant id and a dot"
            )
        if not seg["canary"]["ran"] and seg["canary"]["foreign_hits"]:
            bad.append("segregation.canary: foreign hits without a run")
    return bad


def check(doc: dict[str, Any]) -> list[str]:
    sc = doc["scorecard"]
    bad = keys_unique(list(sc["hard_zeros"]), "scorecard.hard_zeros")
    bad += keys_unique([d["key"] for d in sc["hard_zero_defs"]], "scorecard.hard_zero_defs")
    for part in (_tenant_rules, _case_rules, _approval_rules, _ledger_rules, _context_rules):
        bad += part(doc)
    return bad
