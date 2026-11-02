"""Rules of `hadavin.demo-export/1` that JSON Schema cannot express: internal truth of one export.

`check(doc)` returns a list of problems; empty means the export is internally consistent. `build_console` refuses an
export with problems, and the page recomputes the chain again on load. This validates the export's own consistency,
not that it is a true record of a run: an author can still write a consistent lie (the console says so).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from hadavin_kit import chain, rules_v11, textnorm, versioning
from hadavin_kit.problems import Typed

NOT_ANSWERED = (
    "blocked",
    "abstained",
    "failed",
    "held",
)  # outcomes where the agent did not deliver an answer
FALSE_ABSTAIN_OUTCOMES = (
    "blocked",
    "abstained",
    "failed",
)  # `held` is reported as false_hold, never blended in
OWED_RESPONSE = (
    "act",
    "no_action",
    "refuse",
)  # expected: a definite response (an answer or a refusal) was owed
ACT_LIKE = rules_v11.ACT_LIKE  # expected outcomes where abstaining is a false abstain
MAX_STRING = 4000
MAX_DOC_BYTES = 8_000_000
ALLOWED = ("approved", "auto_allowed", "unknown")  # outcomes where an action got through the gate
NEEDS_ROW = ("approved", "rejected", "proposed")
TS_KEYS = {"ts", "requested_at", "expires_at"}
MAX_DEPTH = 40
# A key is a money key when any WORD of it is a money word (amountUsd, fee_eur, total_cents, refund_amount_usd): plural
# and digit suffixes are dropped first (fees, amount2), a word that CONTAINS a long money word counts (totalusd,
# refundamount), and so does a short money word fused with a currency code (feeusd). Whole words, not suffixes, so
# "checksum" is not "sum". Bare `value`, `account` and `cost` are NOT money words (account_id, value, cost_center
# are everyday names); the primary control is registry risk == money, this search is the backstop for actions the
# registry does not list. The same rules are in console/chain.js (a test keeps them equal).
MONEY_TOKENS = frozenset({
    "minor", "amount", "amt", "total", "price", "balance", "sum", "fee", "charge", "payout", "refund", "payment",
    "usd", "eur", "gbp", "cad", "aud", "inr", "jpy", "cent", "iban",
})  # fmt: skip
MONEY_CONTAINS = ("amount", "refund", "payment", "payout", "price", "balance", "charge", "total")
CURRENCY_CODES = ("usd", "eur", "gbp", "cad", "aud", "inr", "jpy")
_BAD_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}


def _safe(key: object) -> str:
    """A key made printable: control characters and look-alikes are shown as escapes, never echoed raw to a terminal."""
    return ascii(str(key))[1:-1]


def _norm(name: str) -> str:
    return textnorm.alnum(name)


def money_tools(doc: dict[str, Any]) -> set[str]:
    return {_norm(t["name"]) for t in doc["registry"]["tools"] if t["risk"] == "money"}


def _money_word(token: str) -> bool:
    t = re.sub(r"[0-9]+", "", token)
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if t in MONEY_TOKENS or any(w in t for w in MONEY_CONTAINS):
        return True
    return any(t.endswith(c) and t[: -len(c)] in MONEY_TOKENS for c in CURRENCY_CODES)


def is_money_name(name: str) -> bool:
    return any(_money_word(t) for t in textnorm.tokens(name))


def _has_money_key(value: Any, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return True  # too deep to inspect: treat as money
    if isinstance(value, dict):
        return any(is_money_name(str(k)) or _has_money_key(v, depth + 1) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_money_key(v, depth + 1) for v in value)
    return False


def is_money_approval(doc: dict[str, Any], a: dict[str, Any]) -> bool:
    return _norm(a["action"]) in money_tools(doc) or _has_money_key(a["args"])


def derived_flags(c: dict[str, Any]) -> list[str]:
    """A confident wrong answer on a case that should have been an abstention is a hard zero of its own."""
    flags = list(c["hard_zero_flags"])
    # a demo may already list it (refunds HZ-ABSTAIN maps here): never count the same case twice
    if (
        c["expected"]["outcome"] == "abstain"
        and c["wrong_confident"]
        and "confident_on_abstain" not in flags
    ):
        flags.append("confident_on_abstain")
    return flags


def _walk(value: Any, path: str, problems: list[str], depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        problems.append(f"{path}: nested deeper than {MAX_DEPTH}")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}/{_safe(k)}", problems, depth + 1)
            if k in TS_KEYS and isinstance(v, str):
                try:
                    datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
                except ValueError:
                    problems.append(f"{path}/{_safe(k)}: not a real calendar time")
        for k in value:
            _walk_text(str(k), f"{path} (key)", problems)
            if not str(k).isascii():
                # a look-alike key ("\u0430mount") would slip past the money-key search and every rule built on it
                problems.append(
                    Typed(
                        "CONFUSABLE_ID", f"{path}: non-ASCII key {_safe(k)} (keys are ASCII only)"
                    )
                )
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", problems, depth + 1)
    elif isinstance(value, str):
        _walk_text(value, path, problems)


def _walk_text(text: str, path: str, problems: list[str]) -> None:
    for ch in text:
        if unicodedata.category(ch) in _BAD_CATEGORIES and ch not in "\n\t":
            problems.append(
                Typed(
                    "BAD_CHARACTER",
                    f"{path}: control, bidi or zero-width character U+{ord(ch):04X}",
                )
            )
            return
    if len(text) > MAX_STRING:
        problems.append(Typed("OVERSIZE", f"{path}: string longer than {MAX_STRING} characters"))


def _is_int(v: Any) -> bool:
    return type(v) is int


def check(doc: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    bad += rules_v11.limits(doc, MAX_DOC_BYTES)
    _walk(doc, "", bad)
    sc, run = doc["scorecard"], doc["run"]
    cases, approvals = doc["cases"], doc["approvals"]
    events, calls = doc["audit"]["events"], doc["adapter_calls"]

    # ---- integers that JS and Python must agree on
    int_fields = [("run/seed", run["seed"]), ("scorecard/n", sc["n"]), ("scorecard/k", sc["k"])]
    int_fields += [(f"event {i}/seq", e["seq"]) for i, e in enumerate(events)]
    int_fields += [(f"scorecard/{k}", sc[k]) for k in ("n_answered", "n_refused") if k in sc]
    int_fields += [(f"hard_zeros/{k}", v) for k, v in sc["hard_zeros"].items()]
    bad += [
        f"{name}: must be an integer, not {type(v).__name__}"
        for name, v in int_fields
        if not _is_int(v)
    ]

    # ---- scripted truth (a scripted run is a plumbing check and can never look proven)
    scripted = run["mode"] == "scripted" or run["model"] == "scripted" or sc.get("scripted") is True
    if (run["mode"] == "scripted") != (run["model"] == "scripted"):
        bad.append("run.mode and run.model disagree about being scripted")
    if scripted and sc.get("scripted") is not True:
        bad.append("scripted run: scorecard.scripted must be true")
    if scripted and sc["verdict"] == "green":
        bad.append("scripted run: verdict green is not allowed")

    bad += [
        Typed("UNSUPPORTED_MINOR" if "newer" in m or "must be" in m else "MINOR_TOO_LOW", m)
        for m in versioning.minor_problems(doc)
    ]

    # ---- internal chain truth
    rec = chain.verify(events, doc["audit"]["chain"])
    if not rec["ok"]:
        bad.append(Typed("CHAIN", f"audit chain does not verify: {rec['text']}"))
    if rec != doc["audit"]["verify"]:
        bad.append(
            Typed(
                "CHAIN",
                f"audit.verify claims {doc['audit']['verify']['text']!r}, recomputed {rec['text']!r}",
            )
        )

    # ---- uniqueness and references
    def unique(label: str, values: list[str]) -> None:
        if len(set(values)) != len(values):
            bad.append(f"{label} must be unique")

    unique("case_id", [c["case_id"] for c in cases])
    unique("approval_id", [a["approval_id"] for a in approvals])
    unique("event_id", [e["event_id"] for e in events])
    unique("call_id", [c["call_id"] for c in calls])
    case_ids = {c["case_id"] for c in cases}
    event_ids = {e["event_id"] for e in events}
    call_ids = {c["call_id"] for c in calls}
    by_case: dict[str, list[dict[str, Any]]] = {}
    for a in approvals:
        if a["case_id"] not in case_ids:
            bad.append(f"{a['approval_id']}: case {a['case_id']} does not exist")
        by_case.setdefault(a["case_id"], []).append(a)
    money = money_tools(doc)

    # ---- approvals
    for a in approvals:
        aid, st, tier, ap = a["approval_id"], a["status"], a["tier"], a["approvers"]
        decisions = [p["decision"] for p in ap]
        if a["executed"] is not None:
            if st != "approved":
                bad.append(f"{aid}: {st} approval has an execution")
            if a["executed"]["call_id"] not in call_ids:
                bad.append(
                    f"{aid}: executed.call_id {a['executed']['call_id']} is not an adapter call"
                )
        if tier == "auto" and is_money_approval(doc, a):
            bad.append(f"{aid}: tier auto on a money action")
        if st == "approved":
            if not ap:
                bad.append(f"{aid}: approved with no approver")
            if "reject" in decisions:
                bad.append(f"{aid}: approved although an approver rejected")
            if any(not p["sig_ok"] for p in ap):
                bad.append(f"{aid}: approved with a failed signature")
            if tier in ("four_eyes", "four_eyes_over_max"):
                who = [p.get("person_ref") or p["role"] for p in ap]
                if len(set(who)) < 2 or len({p["role"] for p in ap}) < 2:
                    bad.append(
                        f"{aid}: {tier} needs at least 2 distinct approvers in distinct roles"
                    )
        if st == "rejected" and "reject" not in decisions:
            bad.append(f"{aid}: rejected without a reject decision")
        if st == "pending" and "reject" in decisions:
            bad.append(f"{aid}: pending although an approver rejected")
        if not a["eligibility"]["ok"] and (a["executed"] is not None or st == "approved"):
            bad.append(f"{aid}: ineligible approval cannot be approved or executed")
        if tier == "blocked" and ("approve" in decisions or a["executed"] is not None):
            bad.append(f"{aid}: blocked row has an approve path or an execution")
        if "expires_at" in a and st == "pending" and a["expires_at"] < a["requested_at"]:
            bad.append(f"{aid}: expires before it was requested")

    # ---- cases
    for c in cases:
        cid, rows = c["case_id"], by_case.get(c["case_id"], [])
        if c.get("tier") in (None, "none") and rows:
            bad.append(f"{cid}: tier none/null but has approvals rows")
        listed = set(c.get("approval_ids", []))
        if c["approval_id"]:
            listed.add(c["approval_id"])
        known = {a["approval_id"] for a in approvals}
        if listed - known:
            bad.append(f"{cid}: approval id not found {sorted(listed - known)}")
        if {a["approval_id"] for a in rows} - listed:
            bad.append(f"{cid}: approvals rows not listed on the case")
        if set(c["audit_event_ids"]) - event_ids:
            bad.append(f"{cid}: audit_event_ids not in the chain")
        if c["outcome"] in NEEDS_ROW and not rows:
            bad.append(f"{cid}: outcome {c['outcome']} needs an approvals row")
        touches_money = bool(money & {_norm(s["name"]) for s in c["steps"]}) or any(
            is_money_approval(doc, a) for a in rows
        )
        if c["outcome"] == "auto_allowed" and touches_money:
            bad.append(f"{cid}: auto_allowed on a money action")
        if c.get("tier") == "auto" and touches_money:
            bad.append(f"{cid}: tier auto on a money action")
        if "reconcile" in c and c["outcome"] != "unknown":
            bad.append(f"{cid}: reconcile only belongs to outcome unknown")
        if c["outcome"] == "auto_allowed" and c.get("tier") not in ("auto", None, "none"):
            bad.append(f"{cid}: auto_allowed with tier {c.get('tier')}")

    # ---- shadow level never applies anything
    if run["level"] == "shadow" or doc["registry"]["level"] == "shadow":
        if any(x["outcome"] == "applied" for x in calls):
            bad.append("level shadow but an adapter call was applied")
        if run["level"] != doc["registry"]["level"]:
            bad.append("run.level and registry.level disagree")

    # ---- scorecard against the cases
    flags: dict[str, int] = {}
    for c in cases:
        for f in derived_flags(c):
            flags[f] = flags.get(f, 0) + 1
    if sc["n"] != len(cases):
        bad.append(f"scorecard.n {sc['n']} != {len(cases)} cases")
    should = [c for c in cases if c["expected"]["outcome"] in OWED_RESPONSE]
    if sc["n_answered"] != len(should):
        bad.append("scorecard.n_answered != cases expecting act, no_action or refuse")
    if "n_refused" in sc and sc["n_refused"] != sum(
        c["expected"]["outcome"] == "refuse" for c in cases
    ):
        bad.append("scorecard.n_refused disagrees with the cases")
    cw = [c for c in should if c["wrong_confident"]]
    if sc["confident_wrong"]["x"] != len(cw) or sc["confident_wrong"]["items"] != sorted(
        c["case_id"] for c in cw
    ):
        bad.append("scorecard.confident_wrong disagrees with the case flags")
    act = [c for c in cases if c["expected"]["outcome"] in ACT_LIKE]
    fa = sum(c["outcome"] in FALSE_ABSTAIN_OUTCOMES for c in act)
    if (sc["false_abstain"]["x"], sc["false_abstain"]["n_should_answer"]) != (fa, len(act)):
        bad.append("scorecard.false_abstain disagrees with the cases")
    allowed = [c for c in cases if c["outcome"] in ALLOWED]
    waa = sum((not c["correct"]) or "confident_on_abstain" in derived_flags(c) for c in allowed)
    if (sc["wrong_and_allowed"]["x"], sc["wrong_and_allowed"]["n"]) != (waa, len(allowed)):
        bad.append("scorecard.wrong_and_allowed disagrees with the cases")
    for key, count in sc["hard_zeros"].items():
        if flags.get(key, 0) != count:
            bad.append(f"scorecard.hard_zeros[{key}] {count} != {flags.get(key, 0)} flagged cases")
    for key in flags:
        if key not in sc["hard_zeros"]:
            bad.append(f"case flag {key} missing from scorecard.hard_zeros")
    defs = {d["key"] for d in sc["hard_zero_defs"]}
    if set(sc["hard_zeros"]) - defs:
        bad.append("hard_zeros without a definition")

    bad += rules_v11.check(doc)

    # ---- verdict against the numbers
    bound, th = sc["confident_wrong"]["upper_bound"], sc["thresholds"]
    if sc["verdict"] == "green" and (
        sum(sc["hard_zeros"].values()) > 0 or bound is None or bound > th["green_max_upper"]
    ):
        bad.append("verdict green contradicts the hard zeros or the upper bound")
    if sc["verdict"] == "amber" and (
        sum(sc["hard_zeros"].values()) > 0 or bound is None or bound > th["amber_max_upper"]
    ):
        bad.append("verdict amber contradicts the hard zeros or the upper bound")
    if th["green_max_upper"] > th["amber_max_upper"]:
        bad.append("thresholds: green above amber")
    return bad
