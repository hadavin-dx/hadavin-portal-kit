"""The vendoring contract of the kit: which files are runtime, their hashes, and a check that a copy is intact.

    python -m hadavin_kit.kitsum --check [DIR]      verify DIR (default: this kit) against its KIT.lock
    python -m hadavin_kit.kitsum --print [DIR]      print the per-file hashes and the tree hash

A demo VENDORS the kit: it copies the runtime files (hadavin_kit/*.py except docgen and testing, schema/, console/) from
a MERGED kit commit with `tools/vendor.py copy`, which writes `vendor/KIT.lock` in the demo (the merged commit, a SHA-256 per
file, a tree hash and the minimum pins; the files sit in `vendor/portal-kit/`). `--check` fails on a changed, missing or
unlisted file. The kit's own repository keeps `KIT-SHA256SUMS` (sha256sum lines, no commit) and a test keeps it current.
One format, shared with the demos' sync check. tests/ and tools/ are not
vendored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from hadavin_kit import paths

LOCK_FORMAT = "hadavin-kit-lock/1"
NOT_VENDORED = (
    "docgen.py",
    "testing.py",
    "asks.v1.json",
)  # development helpers and the ask ledger: they read examples and fixtures that are not shipped
CONSOLE_FILES = ("app.css", "app.js", "build_console.py", "chain.js", "index.template.html")
SCHEMA_FILES = ("demo-export.v1.json",)


def runtime_files(root: Path) -> list[str]:
    """Relative POSIX paths of every runtime file that exists under `root` (sorted)."""
    found = [
        f"hadavin_kit/{p.name}"
        for p in (root / "hadavin_kit").glob("*.py")
        if p.name not in NOT_VENDORED
    ]
    found += [f"schema/{n}" for n in SCHEMA_FILES if (root / "schema" / n).is_file()]
    found += [f"console/{n}" for n in CONSOLE_FILES if (root / "console" / n).is_file()]
    return sorted(found)


def _sha(path: Path) -> str:
    """sha256 of the RAW bytes: no newline normalisation. A demo repository therefore needs
    `vendor/portal-kit/** -text` in .gitattributes, or a CRLF checkout changes the hashes; in this repository the
    runtime files are pinned to LF by .gitattributes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digests(root: Path) -> dict[str, str]:
    return {rel: _sha(root / rel) for rel in runtime_files(root)}


def tree_hash(files: dict[str, str]) -> str:
    """sha256 of the sorted "<sha>  <relpath>" lines: the format the demos' sync check verifies."""
    return hashlib.sha256("".join(f"{files[k]}  {k}\n" for k in sorted(files)).encode()).hexdigest()


def sums_text(files: dict[str, str]) -> str:
    """KIT-SHA256SUMS: sha256sum lines `<64 hex>  <relpath>`, sorted."""
    return "".join(f"{files[k]}  {k}\n" for k in sorted(files))


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            sha, rel = line.split("  ", 1)
            out[rel] = sha
    return out


def expected_names() -> list[str]:
    return sorted([f"console/{n}" for n in CONSOLE_FILES] + [f"schema/{n}" for n in SCHEMA_FILES])


_IGNORED_NAMES = (".DS_Store", "Thumbs.db")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _ignorable(p: Path) -> bool:
    return p.name in _IGNORED_NAMES or p.suffix == ".pyc" or "__pycache__" in p.parts


def _vendored_files(root: Path, exclusive: bool) -> list[str]:
    """Every file that is part of the vendored tree, so a stray one is "unlisted". The development helpers
    (docgen.py, testing.py, asks.v1.json) are NOT exempt: they are never vendored, so one that appears is planted.
    `exclusive` (a lock with a vendor_dir: `root` is the vendor directory) also covers files at the root and in any
    other directory; otherwise only hadavin_kit/, schema/ and console/ are walked (a kit checkout holds tests too)."""
    top = [root] if exclusive else [root / d for d in ("hadavin_kit", "schema", "console")]
    return sorted(
        p.relative_to(root).as_posix()
        for d in top
        if d.is_dir()
        for p in d.rglob("*")
        if (p.is_file() or p.is_symlink())
        and not _ignorable(p)
        and (exclusive or p.name not in NOT_VENDORED)  # a kit checkout holds the helpers
    )


def lock_shape_problems(lock: Any) -> list[str]:
    """A KIT.lock that is not a JSON object with a dict of 64-hex hashes over safe relative paths is reported, never raised on."""
    if not isinstance(lock, dict) or not isinstance(lock.get("files"), dict):
        return ["not a KIT.lock: a JSON object with a `files` object is required"]
    out = []
    for rel, sha in lock["files"].items():
        parts = rel.split("/") if isinstance(rel, str) else []
        if (
            not parts
            or rel.startswith("/")
            or ".." in parts
            or "" in parts
            or "\\" in rel
            or ":" in rel
        ):
            out.append("a file path in the lock is not a safe relative path")
        elif not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            out.append(f"{rel}: the lock's hash is not 64 lowercase hex digits")
    return out


def vendor_dir_of(demo: Path, lock: dict[str, Any]) -> tuple[Path | None, str | None]:
    """DEMO/<vendor_dir>, confined to the demo: a vendor_dir that leaves it (absolute, `..`, a link) is a problem."""
    rel = lock.get("vendor_dir") or "vendor/portal-kit"
    parts = rel.split("/") if isinstance(rel, str) else []
    if (
        not parts
        or rel.startswith("/")
        or ":" in rel
        or ".." in parts
        or "" in parts
        or "\\" in rel
    ):
        return None, "vendor_dir is not a relative path inside the demo"
    target = demo / rel
    probe = demo
    for part in parts:
        probe = probe / part
        if probe.is_symlink():
            return None, "vendor_dir passes through a link"
    if not target.resolve().is_relative_to(demo.resolve()):
        return None, "vendor_dir leaves the demo"
    return target, None


def check(root: Path, lock_path: Path | None = None) -> list[str]:
    """Problems between the files under `root` and a lock; an empty list means the copy is intact.

    `lock_path` is a `KIT.lock` (JSON: kit_commit, files, tree_sha256; every file under `root` must be listed) or a
    `KIT-SHA256SUMS` (sha256sum lines; only the runtime files are compared, the kit repository holds more).
    This verifies against the LOCK only: a demo that edits vendored files and regenerates its own lock passes here;
    `core_sync_check --kit <kit checkout at kit_commit>` is what compares the lock with the kit."""
    lock_path = lock_path or (root / "KIT-SHA256SUMS")
    strict = lock_path.suffix == ".lock"
    try:
        text = lock_path.read_text(encoding="utf-8")
        lock: Any = json.loads(text) if strict else {}
        if strict:
            shape = lock_shape_problems(lock)
            if shape:
                return [f"{lock_path.name}: {m}" for m in shape]
            want: dict[str, str] = lock["files"]
        else:
            want = parse_sums(text)
    except (OSError, ValueError, RecursionError) as err:
        return [f"{lock_path.name} is unreadable: {type(err).__name__}"]
    have = digests(root)
    out = [f"{rel}: missing" for rel in sorted(rel for rel in want if not (root / rel).is_file())]
    if strict:
        everything = {
            rel: _sha(root / rel)
            for rel in _vendored_files(root, exclusive=bool(lock.get("vendor_dir")))
            if (root / rel).is_file()
        }
        out += [
            f"{rel}: not in the lock (unlisted file)" for rel in sorted(set(everything) - set(want))
        ]
        out += [
            f"{rel}: changed since it was vendored"
            for rel in sorted(set(everything) & set(want))
            if everything[rel] != want[rel]
        ]
        if tree_hash(want) != lock.get("tree_sha256"):
            out.append("the lock's own tree hash does not match its file list")
    else:
        out += [
            f"{rel}: not in KIT-SHA256SUMS (extra runtime file)"
            for rel in sorted(set(have) - set(want))
        ]
        out += [
            f"{rel}: changed since the sums were written"
            for rel in sorted(set(have) & set(want))
            if have[rel] != want[rel]
        ]
    return out


def lock_data(root: Path) -> dict[str, Any]:
    files = digests(root)
    return {"files": files, "tree_sha256": tree_hash(files)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kitsum", description=__doc__.splitlines()[0])
    ap.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=paths.ROOT,
        help="a demo repository (vendor/KIT.lock) or a kit root",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--print", action="store_true", dest="show")
    args = ap.parse_args(argv)
    if args.show:
        data = lock_data(args.directory)
        for rel, h in data["files"].items():
            print(f"{h}  {rel}")
        print(f"tree {data['tree_sha256']}")
        return 0
    d = args.directory
    lock_path = d / "vendor" / "KIT.lock"
    if lock_path.is_file():  # a demo repository: the files sit where the lock says, inside the demo
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            lock = None
        shape = lock_shape_problems(lock)
        vend, bad = (None, None) if shape else vendor_dir_of(d, lock)
        if shape:
            problems = [f"KIT.lock: {m}" for m in shape]
        elif bad or vend is None:
            problems = [f"KIT.lock: {bad}"]
        else:
            problems = check(vend, lock_path)
    else:
        problems = check(d)
    for p in problems:
        print(f"kitsum: {p}")
    print(f"kitsum {args.directory}: {'OK' if not problems else 'MISMATCH'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
