"""Mutation harness for the export validator, its helpers and the JSON schema.

    python tools/mutation.py [--all | --limit N] [--seed S] [--workers W] [--targets ...] [--only REPORT]

Order of work (each step stops the run with its own exit code):
1. BASELINE: the unmutated test subset must be green. A red baseline makes every "killed" meaningless, so the harness
   refuses to continue (exit 3).
2. CAN IT SEE: a deliberate break (the audit chain's sequence check inverted) must be killed, and a no-op change (a
   comment appended) must survive. If the first survives the harness is blind (exit 4); if the second is "killed" the
   tests are flaky or the harness is wrong (exit 5).
3. MUTANTS. Python targets: comparison operators, a boundary literal compared against (n -> n + 1), and/or, `not`
   removal, `return True/False` flips, deleted `bad.append(...)` rules and `bad += ...` rule groups. The JSON schema:
   `additionalProperties: false` -> true, a `required` entry removed, an enum value removed, a `pattern` removed.
   Each mutant runs in its own copy of the kit. A mutant is KILLED only when pytest exits with code 1 (tests ran and
   failed). Exit code 0 = SURVIVED. Any other code (2 interrupted, 3 internal error, 4 usage/collection error, 5 no
   tests) or a timeout (retried once) is INCONCLUSIVE and fails the run: a broken workspace must not count as a kill.
   Survivors fail the run unless listed in `mutation_equivalents.json` with a reason; with `--all`, a listed entry that
   matches no mutant also fails the run, so the list cannot rot.
CI runs a seeded sample (a different seed each run); the release gate is `--all`.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
SCHEMA_REL = "schema/demo-export.v1.json"
DEFAULT_TARGETS = (
    "export_rules.py", "rules_v11.py", "validate.py", "versioning.py", "chain.py", "honest.py",
    "kitcheck.py", "strict_json.py", "problems.py", "evalcore.py", "textnorm.py", "kitsum.py", "paths.py",
    "console/build_console.py", "tools/vendor.py", SCHEMA_REL,
)  # fmt: skip
# NOT mutated, on purpose: console/chain.js and console/app.js (a JavaScript mutation tool such as Stryker is not
# installed; the JS is covered by the node parity tests and the browser tests, and their gaps are listed in CONTRACT),
# docgen.py and testing.py (development helpers that are not vendored), scripted_llm.py and pytest_plugin.py (replay and
# socket-blocking helpers for demos' own tests; their behaviour is exercised by the demos' suites).
TEST_FILES = (
    "test_rules_v11.py", "test_rules_v10.py", "test_validate_typed.py", "test_versioning.py", "test_kitcheck.py",
    "test_amendments.py", "test_kit_core.py", "test_review_round.py", "test_boundaries.py", "test_console_v11.py",
    "test_hostile.py", "test_evalcore_gates.py", "test_schema_walk.py", "test_additive_diff.py", "test_vendor.py",
    "test_review3.py", "test_pinned_constants.py", "test_survivors3.py", "test_review4.py",
)  # fmt: skip
EQUIVALENTS = HERE / "mutation_equivalents.json"
TIMEOUT_S = 420
# Test files that kill a target's mutants, run FIRST for that target (pytest stops at the first failure, so a killing
# test in the 15th file otherwise costs the whole prefix: the eight build_console mutants that timed out in round 3 are
# killed by test_vendor.py::test_matching_kit_commit_passes_and_absence_is_noted, found only after 116 s on a quiet host).
FIRST = {
    "console/build_console.py": (
        "test_vendor.py",
        "test_console_v11.py",
        "test_boundaries.py",
        "test_kitcheck.py",
    ),
    "tools/vendor.py": ("test_review4.py", "test_vendor.py", "test_survivors3.py"),
}
IN_KIT_DIRS = (
    "console/",
    "schema/",
    "tools/",
)  # targets named relative to ; the rest are in hadavin_kit/

CMP_SWAP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
    ast.GtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}  # fmt: skip


@dataclass(frozen=True)
class Mutant:
    file: str
    index: int
    kind: str
    line: int
    func: str
    text: str

    @property
    def key(self) -> str:
        """Stable across line shifts: file, enclosing function, kind and the FULL source text of the site."""
        return f"{self.file}::{self.func}::{self.kind}::{self.text}"


# ------------------------------------------------------------------------------------------ python mutants
def _is_rule_append(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "append"
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id in ("bad", "out", "problems")
    ) or (
        isinstance(node, ast.AugAssign)
        and isinstance(node.op, ast.Add)
        and isinstance(node.target, ast.Name)
        and node.target.id in ("bad", "out", "problems")
    )


def _eligible(node: ast.AST) -> str | None:
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in CMP_SWAP:
        return f"cmp {type(node.ops[0]).__name__}->{CMP_SWAP[type(node.ops[0])].__name__}"
    if (
        isinstance(node, ast.Compare)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and type(node.comparators[0].value) is int
    ):
        return "boundary literal +1"
    if isinstance(node, ast.BoolOp):
        return "boolop " + ("and->or" if isinstance(node.op, ast.And) else "or->and")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return "not removed"
    if (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, bool)
    ):
        return "return bool flipped"
    if _is_rule_append(node):
        return "rule deleted"
    return None


def _split_alts(s: str) -> list[str]:
    """Split a regular expression on its top-level `|` (not inside () or [], not escaped)."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur += s[i : i + 2]
            i += 2
            continue
        depth += (ch in "([") - (ch in ")]")
        if ch == "|" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    return [*out, cur]


_GROUP = re.compile(r"\((?:\?[:=!])?([^()]*\|[^()]*)\)")
_REGEX_FUNCS = {"compile", "search", "match", "fullmatch", "sub", "findall", "finditer", "split"}


def _regex_consts(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in _REGEX_FUNCS
        ):
            ids |= {
                id(a)
                for a in n.args[:1]
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            }
    return ids


def _string_kinds(node: ast.Constant, regex_ids: frozenset[int]) -> list[str]:
    v, out = node.value, []
    if id(node) in regex_ids:
        if len(_split_alts(v)) > 1:
            out += [f"regex alternation drop[{i}]" for i in range(len(_split_alts(v)))]
        for g, m in enumerate(_GROUP.finditer(v)):
            out += [f"regex group{g} drop[{i}]" for i in range(len(m.group(1).split("|")))]
    elif ("'none'" in v or "-src" in v) and "; " in v:  # Content-Security-Policy directives
        out += [f"directive drop[{i}]" for i in range(len(v.split("; ")))]
    return out


def _literal_kinds(node: ast.AST) -> list[str]:
    if (
        isinstance(node, ast.Tuple | ast.List | ast.Set)
        and len(node.elts) >= 2
        and all(isinstance(e, ast.Constant) and type(e.value) in (str, int) for e in node.elts)
    ):
        return [f"literal drop[{i}]" for i in range(len(node.elts))]
    return []


def _kinds(node: ast.AST, regex_ids: frozenset[int] = frozenset()) -> list[str]:
    """A Compare can carry two mutations (its operator and its literal); each is its own mutant."""
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
    ):
        return []  # the `if __name__ == "__main__"` entry guard: mutating it only breaks the import, not a behaviour
    out = []
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in CMP_SWAP:
        out.append(f"cmp {type(node.ops[0]).__name__}->{CMP_SWAP[type(node.ops[0])].__name__}")
    if (
        isinstance(node, ast.Compare)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and type(node.comparators[0].value) is int
    ):
        out.append("boundary literal +1")
    if not out:
        kind = _eligible(node)
        if kind:
            out.append(kind)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out += _string_kinds(node, regex_ids)
    out += _literal_kinds(node)
    return out


def _sites(tree: ast.AST) -> list[tuple[ast.AST, str, str]]:
    """(node, kind, enclosing function) in a fixed order shared by enumeration and mutation."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    out = []
    regex_ids = frozenset(_regex_consts(tree))
    typing_ids = frozenset(  # Literal["a", "b"] in an annotation is type information, not behaviour
        id(n.slice)
        for n in ast.walk(tree)
        if isinstance(n, ast.Subscript)
        and isinstance(n.value, ast.Name)
        and n.value.id == "Literal"
    )
    for node in ast.walk(tree):
        if id(node) in typing_ids:
            continue
        for kind in _kinds(node, regex_ids):
            p, func = parents.get(id(node)), "<module>"
            while p is not None:
                if isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef):
                    func = p.name
                    break
                p = parents.get(id(p))
            out.append((node, kind, func))
    return out


def src_path(rel: str) -> Path:
    return KIT / rel if rel.startswith(IN_KIT_DIRS) else KIT / "hadavin_kit" / rel


def enumerate_python(rel: str) -> list[Mutant]:
    tree = ast.parse(src_path(rel).read_text(encoding="utf-8"))
    return [
        Mutant(rel, i, kind, node.lineno, func, ast.unparse(node))
        for i, (node, kind, func) in enumerate(_sites(tree))
    ]


def _drop(items: list, i: int) -> list:
    return [x for j, x in enumerate(items) if j != i]


def _mutate(node: ast.AST, kind: str) -> ast.AST:
    if kind.startswith("literal drop["):
        node.elts = _drop(node.elts, int(kind[len("literal drop[") : -1]))  # type: ignore[attr-defined]
        return node
    if kind.startswith("directive drop["):
        parts = node.value.split("; ")  # type: ignore[attr-defined]
        node.value = "; ".join(_drop(parts, int(kind[len("directive drop[") : -1])))  # type: ignore[attr-defined]
        return node
    if kind.startswith("regex alternation drop["):
        parts = _split_alts(node.value)  # type: ignore[attr-defined]
        node.value = "|".join(_drop(parts, int(kind[len("regex alternation drop[") : -1])))  # type: ignore[attr-defined]
        return node
    if kind.startswith("regex group"):
        g, i = kind[len("regex group") :].rstrip("]").split(" drop[")
        m = list(_GROUP.finditer(node.value))[int(g)]  # type: ignore[attr-defined]
        alts = _drop(m.group(1).split("|"), int(i))
        node.value = node.value[: m.start(1)] + "|".join(alts) + node.value[m.end(1) :]  # type: ignore[attr-defined]
        return node
    if kind.startswith("cmp"):
        node.ops = [CMP_SWAP[type(node.ops[0])]()]  # type: ignore[attr-defined]
    elif kind == "boundary literal +1":
        node.comparators = [ast.Constant(node.comparators[0].value + 1)]  # type: ignore[attr-defined]
    elif kind.startswith("boolop"):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()  # type: ignore[attr-defined]
    elif kind == "not removed":
        return node.operand  # type: ignore[attr-defined]
    elif kind == "return bool flipped":
        node.value = ast.Constant(not node.value.value)  # type: ignore[attr-defined]
    else:
        return ast.copy_location(ast.Pass(), node)
    return node


class _Swap(ast.NodeTransformer):
    """Mutate the one site at (line, column, kind); that triple is unique among sites."""

    def __init__(self, at: tuple[int, int, str]) -> None:
        self.at, self.done = at, False
        self.regex_ids: frozenset[int] = frozenset()

    def visit(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.Module):
            self.regex_ids = frozenset(_regex_consts(node))
        return super().visit(node)

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if not self.done and getattr(node, "lineno", None) is not None:
            for kind in _kinds(node, self.regex_ids):
                if (node.lineno, node.col_offset, kind) == self.at:
                    self.done = True
                    return _mutate(node, kind)
        return super().generic_visit(node)


def mutate_python(rel: str, index: int) -> str:
    tree = ast.parse(src_path(rel).read_text(encoding="utf-8"))
    node, kind, _ = _sites(tree)[index]
    tree = _Swap((node.lineno, node.col_offset, kind)).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


# ------------------------------------------------------------------------------------------ schema mutants
def _schema_sites(node: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        if node.get("additionalProperties") is False:
            out.append(("schema additionalProperties false->true", path))
        if isinstance(node.get("required"), list):
            out += [(f"schema required -{r}", path) for r in node["required"]]
        if isinstance(node.get("enum"), list) and len(node["enum"]) > 1:
            out += [(f"schema enum -{json.dumps(v)}", path) for v in node["enum"]]
        if "pattern" in node:
            out.append(("schema pattern removed", path))
        for k, v in node.items():
            out += _schema_sites(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _schema_sites(v, f"{path}/{i}")
    return out


def enumerate_schema() -> list[Mutant]:
    schema = json.loads((KIT / SCHEMA_REL).read_text(encoding="utf-8"))
    return [
        Mutant(SCHEMA_REL, i, kind, 0, "", path)
        for i, (kind, path) in enumerate(_schema_sites(schema))
    ]


def mutate_schema(index: int) -> str:
    schema = json.loads((KIT / SCHEMA_REL).read_text(encoding="utf-8"))
    kind, path = _schema_sites(schema)[index]
    node = schema
    for part in [p for p in path.split("/") if p]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if kind.startswith("schema additionalProperties"):
        node["additionalProperties"] = True
    elif kind.startswith("schema required"):
        node["required"].remove(kind.split(" -", 1)[1])
    elif kind.startswith("schema enum"):
        node["enum"].remove(json.loads(kind.split(" -", 1)[1]))
    else:
        del node["pattern"]
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------ running tests
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".git", ".venv", ".ruff_cache", "node_modules", "out"
)


def make_workspace(dst: Path) -> Path:
    """A throw-away copy of the whole repository (the tests expect the repository layout around them)."""
    shutil.copytree(KIT, dst, ignore=COPY_IGNORE, dirs_exist_ok=True)
    return dst


def run_tests(work: Path, first: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    ordered = [*first, *(n for n in TEST_FILES if n not in first)]
    files = [f"tests/{n}" for n in ordered if (KIT / "tests" / n).exists()]
    return subprocess.run(  # noqa: S603 (our own interpreter and fixed arguments)
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "-m", "not browser", *files],
        cwd=work, capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
        env={**os.environ, "HADAVIN_MUTATION_RUN": "1"},  # tells the manifest-currency test to stand aside
    )  # fmt: skip


def verdict_of(returncode: int) -> str:
    """Only pytest's exit code 1 (tests ran and some failed) is a kill."""
    return {0: "survived", 1: "killed"}.get(returncode, "inconclusive")


def run_mutant(code: str, rel_in_kit: str) -> str:
    """'killed', 'survived' or 'inconclusive' for one mutated file (a timeout is retried once)."""
    for attempt in (1, 2):
        with tempfile.TemporaryDirectory(prefix="mut-", ignore_cleanup_errors=True) as tmp:
            work = make_workspace(Path(tmp))
            target = work / (
                rel_in_kit if rel_in_kit.startswith(IN_KIT_DIRS) else f"hadavin_kit/{rel_in_kit}"
            )
            target.write_text(code, encoding="utf-8", newline="\n")
            try:
                return verdict_of(run_tests(work, FIRST.get(rel_in_kit, ())).returncode)
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    return "inconclusive"
    return "inconclusive"


def canary_checks() -> int:
    chain_src = (KIT / "hadavin_kit" / "chain.py").read_text(encoding="utf-8")
    needle = 'ev["seq"] == i'
    if needle not in chain_src:
        print("harness error: canary site not found in chain.py")
        return 4
    verdict = run_mutant(chain_src.replace(needle, 'ev["seq"] != i', 1), "chain.py")
    if verdict != "killed":
        print(f"HARNESS IS BLIND OR BROKEN: the deliberate chain break was {verdict}, not killed")
        return 4
    noop = run_mutant(chain_src + "\n# no-op change\n", "chain.py")
    if noop != "survived":
        print(f"HARNESS OR TESTS ARE FLAKY: a no-op change was {noop}")
        return 5
    print("harness sees: deliberate break killed (pytest exit 1), no-op survived (exit 0)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Runs on a SNAPSHOT of the kit taken at the start, so editing files while a long run is going cannot make the
    mutant list (enumerated once) disagree with the source the mutants are applied to."""
    global KIT
    live = KIT
    snap = Path(tempfile.mkdtemp(prefix="mut-src-"))
    try:
        shutil.copytree(live, snap / "kit", ignore=COPY_IGNORE)
        KIT = snap / "kit"
        return _main(argv)
    finally:
        KIT = live
        shutil.rmtree(snap, ignore_errors=True)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="run every mutant (the release gate)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1, help="CI passes a different seed every run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--targets", nargs="*", default=list(DEFAULT_TARGETS))
    ap.add_argument(
        "--only",
        type=Path,
        help="run only the mutants named by SURVIVOR lines in this earlier report (re-check survivors after adding tests)",
    )
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mut-base-", ignore_cleanup_errors=True) as tmp:
        try:
            base = run_tests(make_workspace(Path(tmp)))
        except subprocess.TimeoutExpired:
            print(
                f"BASELINE IS NOT GREEN: the unmutated tests took longer than {TIMEOUT_S} s (host too slow or loaded)"
            )
            return 3
    if base.returncode != 0:
        print("BASELINE IS NOT GREEN: refusing to mutate (killed would mean nothing)")
        print(base.stdout[-1500:])
        return 3
    print("baseline green")
    code = canary_checks()
    if code:
        return code

    mutants: list[Mutant] = []
    for t in args.targets:
        mutants += enumerate_schema() if t == SCHEMA_REL else enumerate_python(t)
    total = len(mutants)
    if args.only:
        wanted = set()
        for line in args.only.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*SURVIVOR (\S+?) \[(.+?)\] \((.*?)\) (.*)$", line)
            if m:
                wanted.add(f"{m.group(1)}::{m.group(3)}::{m.group(2)}::{m.group(4)}")
        mutants = [m for m in mutants if m.key in wanted]
    elif not args.all and len(mutants) > args.limit:
        mutants = random.Random(args.seed).sample(mutants, args.limit)  # noqa: S311 (a seeded sample, not security)
    allowed = {
        e["key"]: e for e in json.loads(EQUIVALENTS.read_text(encoding="utf-8"))["equivalent"]
    }

    def one(m: Mutant) -> tuple[Mutant, str]:
        try:
            code = (
                mutate_schema(m.index) if m.file == SCHEMA_REL else mutate_python(m.file, m.index)
            )
        except (IndexError, KeyError, SyntaxError, ValueError):
            return m, "error"  # reported, never a crash of the whole run
        return m, run_mutant(code, m.file)

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(one, mutants))
    killed = sum(v == "killed" for _, v in results)
    inconclusive = [m for m, v in results if v == "inconclusive"]
    errors = [m for m, v in results if v == "error"]
    survivors = [m for m, v in results if v == "survived"]
    unexplained = [m for m in survivors if m.key not in allowed]
    print(
        f"mutants: {len(results)} run of {total} generated; killed {killed}; inconclusive {len(inconclusive)}; "
        f"survived {len(survivors)}; unexplained {len(unexplained)}; seed {args.seed}"
        + ("; ALL mutants" if args.all and not args.only else "")
    )
    for m in unexplained:
        print(f"  SURVIVOR {m.file} [{m.kind}] ({m.func}) {m.text}")
    for m in errors:
        print(f"  ERROR (mutant could not be built) {m.file} [{m.kind}] ({m.func}) {m.text}")
    for m in inconclusive:
        print(f"  INCONCLUSIVE {m.file} [{m.kind}] ({m.func}) {m.text}")
    stale = []
    if args.all and not args.only:
        live = {m.key for m in mutants}
        stale = [k for k in allowed if k not in live]
        for k in stale:
            print(f"  STALE equivalent entry (no such mutant): {k}")
    return 1 if unexplained or stale or inconclusive or errors else 0


if __name__ == "__main__":
    sys.exit(main())
