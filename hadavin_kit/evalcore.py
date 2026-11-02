"""Generic eval pieces, copied from the Ask-your-data harness (the ask-your-data demo, evalrun.py,
evalset.py, compare_reports) and made domain-free behind a `Scorer` protocol (SQL-free).
The generic pieces are copied, not imported, from the Ask-your-data harness (design decision); the only core import is
`hadavin_sdk.stats.upper_error_bound` (a pinned, merged core API). TODO: dedupe once the pieces move into the core.

Gate (R45, R164, R188-R191, , ): the one-sided 95 % upper bound of the confident-wrong rate
(`hadavin_sdk.stats.upper_error_bound`). green <= green_max_upper, amber <= amber_max_upper, else not demonstrated.
Any hard zero breached means not demonstrated whatever the bound is. Thresholds live in `lock.json`, frozen
BEFORE the dev set is used. `false_abstain` and `wrong_and_allowed` are mandatory in every scorecard.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from hadavin_sdk.stats import upper_error_bound

from hadavin_kit import strict_json

HUMAN_READ_SALT = "human-read-v1"
DISCLAIMER = "A smoke result on a small held-out set of synthetic data; not live accuracy."
VERDICT_TEXT = {
    "green": "demonstrated: smoke result on a small held-out test set (upper bound within the green level)",
    "amber": "promising, not demonstrated: at most the amber level with 95 % confidence on a small set",
    "not_demonstrated": "not demonstrated",
    "aborted": "no verdict: the run was aborted",
}
_HEX = set("0123456789abcdef")


# ------------------------------------------------------------------------------------------ scorer protocol
class Score(TypedDict):
    correct: bool
    wrong_confident: bool
    hard_zero_flags: list[str]


class Scorer(Protocol):
    def score(self, case: dict[str, Any], trace: dict[str, Any]) -> Score:
        """`case`: gold case (has `expected.outcome`). `trace`: what the agent did. Domain code lives here."""
        ...


@dataclass(frozen=True)
class CaseRun:
    """One scored case. `answered`: the agent proposed/acted (did not abstain, refuse or get blocked).
    `allowed`: an action reached execution or approval-free release (it went through the gate allowed)."""

    case_id: str
    expected: Literal["act", "no_action", "abstain", "refuse"]
    answered: bool
    allowed: bool
    score: Score
    cost_usd: Decimal = Decimal(0)
    latency_ms: int = 0
    held: bool = (
        False  # deliberately held (not answered); counted as false_hold, never as false_abstain
    )


# ------------------------------------------------------------------------------------------------ helpers
def human_read_sample(passing_ids: list[str], fraction: float = 0.2) -> list[str]:
    """Deterministic 20 % sample of passing items for the human read: sorted by sha256(id + salt)."""
    ranked = sorted(
        passing_ids, key=lambda i: hashlib.sha256((i + HUMAN_READ_SALT).encode()).hexdigest()
    )
    return ranked[: math.ceil(fraction * len(passing_ids))]


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


# --------------------------------------------------------------------------------------------- lock (freeze)
class LockError(RuntimeError):
    pass


SPLITS = ("dev", "test")
SECOND_LOOKS = ("aborted", "harness_defect")
STATUSES = ("none", "complete", "aborted")
MAX_HARNESS_DEFECT_LOOKS = 1


def norm_split(split: str) -> str:
    """Case-insensitive, whitespace-tolerant split name; anything else is refused (TEST must not skip the gate)."""
    if not isinstance(split, str) or split.strip().lower() not in SPLITS:
        raise LockError(f"split must be one of {SPLITS}, got {split!r}")
    return split.strip().lower()


@dataclass(frozen=True)
class Thresholds:
    green_max_upper: Decimal = Decimal("0.10")
    amber_max_upper: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        for v in (self.green_max_upper, self.amber_max_upper):
            if not isinstance(v, Decimal) or not v.is_finite() or not Decimal(0) <= v <= Decimal(1):
                raise LockError("thresholds must be finite numbers in [0, 1]")
        if self.green_max_upper > self.amber_max_upper:
            raise LockError("green threshold must not exceed the amber threshold")


@dataclass(frozen=True)
class Lock:
    files: dict[str, str]
    thresholds: Thresholds = Thresholds()
    test_runs: int = 0
    sealed_min_remaining_usd: Decimal = Decimal("3.00")
    last_test_status: str = "none"
    second_look_allowed_for: tuple[str, ...] = SECOND_LOOKS
    harness_defect_looks: int = 0  # how many harness_defect second looks were used (limited)
    run_ids: tuple[str, ...] = ()  # sealed runs already recorded: recording one twice is a no-op

    def to_json(self) -> dict[str, Any]:
        return {
            "files": dict(sorted(self.files.items())),
            "thresholds": {
                "green_max_upper": str(self.thresholds.green_max_upper),
                "amber_max_upper": str(self.thresholds.amber_max_upper),
            },
            "test_runs": self.test_runs,
            "sealed_min_remaining_usd": str(self.sealed_min_remaining_usd),
            "last_test_status": self.last_test_status,
            "second_look_allowed_for": list(self.second_look_allowed_for),
            "harness_defect_looks": self.harness_defect_looks,
            "run_ids": list(self.run_ids),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Lock:
        keys = {
            "files", "thresholds", "test_runs", "sealed_min_remaining_usd", "last_test_status",
            "second_look_allowed_for", "harness_defect_looks", "run_ids",
        }  # fmt: skip
        if not isinstance(d, dict) or set(d) - keys:
            raise LockError(
                f"unknown or malformed lock keys: {sorted(set(d) - keys) if isinstance(d, dict) else d}"
            )
        files = d.get("files")
        if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and len(v) == 64 and set(v) <= _HEX
            for k, v in files.items()
        ):
            raise LockError("lock files must map names to sha256 hex")
        th = d.get("thresholds", {})
        if not isinstance(th, dict) or set(th) - {"green_max_upper", "amber_max_upper"}:
            raise LockError("thresholds malformed")
        try:
            thresholds = Thresholds(
                Decimal(str(th.get("green_max_upper", "0.10"))),
                Decimal(str(th.get("amber_max_upper", "0.15"))),
            )
            remaining = Decimal(str(d.get("sealed_min_remaining_usd", "3.00")))
        except ArithmeticError:
            raise LockError("a lock number is not a number") from None
        if not remaining.is_finite() or remaining < 0:
            raise LockError("sealed_min_remaining_usd must be a finite number >= 0")
        for name in ("test_runs", "harness_defect_looks"):
            v = d.get(name, 0)
            if type(v) is not int or v < 0:
                raise LockError(f"{name} must be an integer >= 0")
        status = d.get("last_test_status", "none")
        if status not in STATUSES:
            raise LockError(f"last_test_status must be one of {STATUSES}")
        looks = d.get("second_look_allowed_for", list(SECOND_LOOKS))
        if not isinstance(looks, list) or not set(looks) <= set(SECOND_LOOKS):
            raise LockError(f"second_look_allowed_for must be a subset of {SECOND_LOOKS}")
        run_ids = d.get("run_ids", [])
        if not isinstance(run_ids, list) or not all(isinstance(r, str) for r in run_ids):
            raise LockError("run_ids must be a list of strings")
        return cls(
            files=dict(files),
            thresholds=thresholds,
            test_runs=d.get("test_runs", 0),
            sealed_min_remaining_usd=remaining,
            last_test_status=status,
            second_look_allowed_for=tuple(looks),
            harness_defect_looks=d.get("harness_defect_looks", 0),
            run_ids=tuple(run_ids),
        )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lock_path(eval_dir: Path) -> Path:
    return Path(eval_dir) / "lock.json"


def read_lock(eval_dir: Path) -> Lock:
    try:
        return Lock.from_json(strict_json.load_object(lock_path(eval_dir)))
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as err:
        raise LockError(f"lock.json missing or invalid: {type(err).__name__}") from None


def write_lock(eval_dir: Path, lock: Lock) -> None:
    path = lock_path(eval_dir)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(lock.to_json(), indent=2, sort_keys=True, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def freeze(eval_dir: Path, names: list[str], thresholds: Thresholds | None = None) -> Lock:
    """Create lock.json from the current files. Refuses when a lock exists: a freeze happens once, before the dev set
    is used; changing a frozen set means a new, visibly different lock made by deleting the old one on purpose."""
    if lock_path(eval_dir).exists():
        raise LockError("lock.json already exists: refusing to overwrite a frozen set")
    lock = Lock(
        files={n: sha256_file(Path(eval_dir) / n) for n in names},
        thresholds=thresholds or Thresholds(),
    )
    write_lock(eval_dir, lock)
    return lock


def verify_files(eval_dir: Path, lock: Lock) -> list[str]:
    """Names of files that are missing or changed since the freeze (empty = intact)."""
    bad = []
    for name, digest in lock.files.items():
        p = Path(eval_dir) / name
        if not p.is_file() or sha256_file(p) != digest:
            bad.append(name)
    return bad


def sealed_gate(lock: Lock, remaining: Decimal, *, split: str, second_look: str | None) -> None:
    """Refuse a sealed (test) run that is not allowed. Raises `LockError`; the caller prints the reason."""
    if norm_split(split) != "test":
        return
    if remaining < lock.sealed_min_remaining_usd:
        raise LockError(
            f"the sealed run needs at least {lock.sealed_min_remaining_usd} remaining, {remaining} left"
        )
    if lock.test_runs >= 1:
        if second_look is None:
            raise LockError(
                "the sealed set tolerates one invocation; a second needs --second-look aborted|harness_defect"
            )
        if second_look not in lock.second_look_allowed_for:
            raise LockError(f"--second-look must be one of {list(lock.second_look_allowed_for)}")
        if second_look == "aborted" and lock.last_test_status != "aborted":
            raise LockError("--second-look aborted: the last sealed run did not abort")
        if (
            second_look == "harness_defect"
            and lock.harness_defect_looks >= MAX_HARNESS_DEFECT_LOOKS
        ):
            raise LockError(
                "--second-look harness_defect was already used; the sealed set is now dev"
            )


def record_run(
    eval_dir: Path, run_id: str, *, split: str, status: str, second_look: str | None = None
) -> Lock:
    """Record a finished sealed run exactly once: increments `test_runs` under an exclusive lock file and the
    atomic replace of lock.json. Recording the same `run_id` again changes nothing. A dev run records nothing."""
    if status not in ("complete", "aborted"):
        raise LockError("status must be complete or aborted")
    if norm_split(split) != "test":
        return read_lock(eval_dir)
    guard = Path(eval_dir) / "lock.json.lock"
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise LockError("another process is recording a run (lock.json.lock exists)") from None
    try:
        lock = read_lock(eval_dir)
        if run_id in lock.run_ids:
            return lock
        lock = replace(
            lock,
            test_runs=lock.test_runs + 1,
            last_test_status=status,
            harness_defect_looks=lock.harness_defect_looks + (second_look == "harness_defect"),
            run_ids=(*lock.run_ids, run_id),
        )
        write_lock(eval_dir, lock)
        return lock
    finally:
        os.close(fd)
        os.unlink(guard)


# --------------------------------------------------------------------------------------------------- the gate
def verdict_for(x: int, n: int, hard_zero_total: int, th: Thresholds) -> tuple[str, float | None]:
    """(green | amber | not_demonstrated, upper bound). `x` confident-wrong items among `n` answered."""
    bound = upper_error_bound(x, n)
    if hard_zero_total > 0 or bound is None:
        return "not_demonstrated", bound
    if Decimal(str(bound)) <= th.green_max_upper:
        return "green", bound
    if Decimal(str(bound)) <= th.amber_max_upper:
        return "amber", bound
    return "not_demonstrated", bound


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def fmt_usd(d: Decimal) -> str:
    return format(d.quantize(Decimal("0.000001")).normalize(), "f")


def build_scorecard(
    runs: list[CaseRun],
    *,
    lock: Lock,
    lock_sha256: str,
    hard_zero_defs: list[dict[str, str]],
    split: Literal["dev", "test"],
    label: str = "smoke",
    informational_flags: frozenset[str] = frozenset(),
    aborted: bool = False,
    scripted: bool = True,
) -> dict[str, Any]:
    """The `scorecard` block of `hadavin.demo-export/1`. One run per case (k = 1)."""
    ids = [r.case_id for r in runs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case_id: build_scorecard scores one run per case (k=1)")
    defs = {d["key"] for d in hard_zero_defs}

    def run_flags(r: CaseRun) -> list[str]:
        # a confident wrong answer where the right behaviour was to abstain is a hard zero of its own
        # a demo may already list it: never count the same case twice
        extra = (
            ["confident_on_abstain"]
            if r.expected == "abstain"
            and r.score["wrong_confident"]
            and "confident_on_abstain" not in r.score["hard_zero_flags"]
            else []
        )
        return [*r.score["hard_zero_flags"], *extra]

    hard_zero_defs = list(hard_zero_defs)
    if not any(d["key"] == "confident_on_abstain" for d in hard_zero_defs):
        hard_zero_defs.append(
            {
                "key": "confident_on_abstain",
                "meaning": "The agent answered confidently and wrongly where it should have abstained.",
            }
        )
    defs = {d["key"] for d in hard_zero_defs}
    flags = Counter(f for r in runs for f in run_flags(r))
    unknown = set(flags) - defs
    if unknown:
        raise ValueError(f"hard zero flags without a definition: {sorted(unknown)}")
    hard_zeros = {k: flags.get(k, 0) for k in sorted(defs)}

    # n_answered (contract amendment 2026-11-01): cases whose expected outcome is act, no_action or refuse, i.e. the
    # agent was expected to give an answer rather than abstain. A false abstain therefore stays in the denominator.
    answered = [r for r in runs if r.expected in ("act", "no_action", "refuse")]
    wrong = [r for r in answered if r.score["wrong_confident"]]
    n_ans, x = len(answered), len(wrong)
    should_answer = [r for r in runs if r.expected in ("act", "no_action")]
    false_abs = [r for r in should_answer if not r.answered and not r.held]
    false_hold = [r for r in should_answer if r.held]
    allowed = [r for r in runs if r.allowed]
    should_hold = [r for r in runs if r.expected in ("abstain", "refuse")]
    held = [r for r in runs if not r.answered]
    verdict, bound = verdict_for(
        x,
        n_ans,
        sum(v for k, v in hard_zeros.items() if k not in informational_flags),
        lock.thresholds,
    )
    if scripted and verdict == "green":
        verdict = "amber"  # a scripted model is a plumbing check: never green
    if aborted:
        verdict = "aborted"
    n_correct = sum(1 for r in runs if r.score["correct"])
    cost = sum((r.cost_usd for r in runs), Decimal(0))
    lat = [r.latency_ms for r in runs]
    card = {
        "split": split,
        "label": label,
        "n": len(runs),
        "n_answered": n_ans,
        "n_refused": sum(1 for r in runs if r.expected == "refuse"),
        "scripted": scripted,
        "pass_rate": (n_correct / len(runs)) if runs else 0.0,
        "k": 1,
        "pass_k": (n_correct / len(runs)) if runs else None,
        "confident_wrong": {
            "x": x,
            "n_answered": n_ans,
            "rate": _ratio(x, n_ans),
            "upper_bound": bound,
            "items": sorted(r.case_id for r in wrong),
        },
        "false_abstain": {
            "x": len(false_abs),
            "n_should_answer": len(should_answer),
            "rate": _ratio(len(false_abs), len(should_answer)),
        },
        "wrong_and_allowed": {
            "x": sum(
                1
                for r in allowed
                if not r.score["correct"] or "confident_on_abstain" in run_flags(r)
            ),
            "n": len(allowed),
        },
        "coverage": {
            "answered_correct": sum(1 for r in should_answer if r.answered and r.score["correct"]),
            "definite": len(should_answer),
        },
        "abstention": {
            "precision": _ratio(
                sum(1 for r in held if r.expected not in ("act", "no_action")), len(held)
            ),
            "recall": _ratio(sum(1 for r in should_hold if not r.answered), len(should_hold)),
        },
        "hard_zeros": hard_zeros,
        "hard_zero_defs": hard_zero_defs,
        "verdict": verdict,
        "verdict_text": VERDICT_TEXT[verdict],
        "thresholds": {
            "green_max_upper": float(lock.thresholds.green_max_upper),
            "amber_max_upper": float(lock.thresholds.amber_max_upper),
        },
        "cost_usd": fmt_usd(cost),
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
        "disclaimer": DISCLAIMER,
        "lock_sha256": lock_sha256,
    }
    if any(r.held for r in runs):
        card["false_hold"] = {"x": len(false_hold), "n": len(should_answer)}
    return card


# ------------------------------------------------------------------------------------------------- compare
class Incomparable(ValueError):
    pass


def compare_scorecards(
    a: dict[str, Any], b: dict[str, Any], informational_flags: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """B against A (A = baseline). Same split and same frozen files (lock hash) or they are not comparable."""
    if a["split"] != b["split"] or a["lock_sha256"] != b["lock_sha256"]:
        raise Incomparable("different split or different frozen files")
    if "aborted" in (a["verdict"], b["verdict"]):
        raise Incomparable("an aborted run has no scores")
    order = {"not_demonstrated": 0, "amber": 1, "green": 2}

    def hz(s: dict[str, Any]) -> int:
        return sum(v for k, v in s["hard_zeros"].items() if k not in informational_flags)

    regress = (
        order[b["verdict"]] < order[a["verdict"]]
        or b["confident_wrong"]["x"] > a["confident_wrong"]["x"]
        or b["false_abstain"]["x"] > a["false_abstain"]["x"]
        or b["wrong_and_allowed"]["x"] > a["wrong_and_allowed"]["x"]
        or hz(b) > hz(a)
    )
    return {
        "verdict": [a["verdict"], b["verdict"]],
        "confident_wrong": [a["confident_wrong"]["x"], b["confident_wrong"]["x"]],
        "false_abstain": [a["false_abstain"]["x"], b["false_abstain"]["x"]],
        "wrong_and_allowed": [a["wrong_and_allowed"]["x"], b["wrong_and_allowed"]["x"]],
        "upper_bound": [a["confident_wrong"]["upper_bound"], b["confident_wrong"]["upper_bound"]],
        "pass_rate": [a["pass_rate"], b["pass_rate"]],
        "cost_usd": [a["cost_usd"], b["cost_usd"]],
        "regression": regress,
    }


# --------------------------------------------------------------------------------------------- report writer
def render_md(title: str, sc: dict[str, Any]) -> str:
    cw, fa, wa = sc["confident_wrong"], sc["false_abstain"], sc["wrong_and_allowed"]
    b = cw["upper_bound"]
    bound = "n/a" if b is None else f"{b * 100:.1f} %"
    th = sc["thresholds"]
    lines = [
        f"# {title}: {sc['split']} ({sc['label']}, n={sc['n']})",
        "",
        f"**Verdict: {sc['verdict_text']}.**",
        "",
        sc["disclaimer"],
        "",
        (
            f"- Confident-wrong: {cw['x']} of {cw['n_answered']} answered; one-sided 95 % upper bound {bound} "
            f"(green <= {th['green_max_upper'] * 100:.0f} %, amber <= {th['amber_max_upper'] * 100:.0f} %)."
        ),
        f"- False abstain: {fa['x']} of {fa['n_should_answer']} cases that should have been answered.",
        f"- Wrong and allowed: {wa['x']} of {wa['n']} actions that went through the gate.",
        f"- Hard zeros: {json.dumps(sc['hard_zeros'], sort_keys=True)}",
        f"- pass@1 {sc['pass_rate']:.3f}; coverage {sc['coverage']['answered_correct']} / {sc['coverage']['definite']}",
        f"- Abstention precision {sc['abstention']['precision']}, recall {sc['abstention']['recall']}",
        f"- Cost {sc['cost_usd']} USD; latency p50 {sc['latency_ms']['p50']} ms, p95 {sc['latency_ms']['p95']} ms",
        f"- Lock {sc['lock_sha256'][:12]}",
    ]
    return "\n".join(lines) + "\n"


def write_report(out_dir: Path, title: str, scorecard: dict[str, Any]) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(scorecard, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "report.md").write_text(render_md(title, scorecard), encoding="utf-8", newline="\n")
