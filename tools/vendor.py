"""Vendor the kit into a demo repository, or refresh the kit's own manifest.

    python tools/vendor.py manifest                      write KIT-SHA256SUMS (kept current by a test)
    python tools/vendor.py copy DEMO --commit SHA        copy the runtime files into DEMO/vendor/portal-kit and write DEMO/vendor/KIT.lock
    python tools/vendor.py verify DIR                    same as python -m hadavin_kit.kitsum --check DIR (a demo repository or a kit root)

Every DEST/DIR is confined to the current directory (an absolute path is accepted only inside it): run from the demo,
`cd demo; python ../portal-kit/tools/vendor.py copy . --commit SHA`. A path outside it exits 2 and says so.

`copy` binds the lock to the bytes it copies. It refuses unless (1) --commit is 40 hex digits, (2) the checkout's HEAD IS
that commit, (3) the kit directory has no uncommitted or untracked change, and (4) the commit is an ancestor of
origin/main (vendor only from a merged commit: a SHA from a branch vanishes on squash-merge). Outside a git checkout it
refuses (fail closed). The hashes are taken from the SOURCE files, the destination directory is wiped first (no stale
file survives a re-vendor) and `kitsum --check` runs on the result. The checks (2) to (4) are skipped only when the
environment variable HADAVIN_VENDOR_ALLOW_UNMERGED=1 is set (tests; there is no command-line flag). The lock records the
minimum pins the runtime imports (hadavin-sdk for evalcore, jsonschema).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
VENDOR_DIR = "vendor/portal-kit"
ALLOW_ENV = "HADAVIN_VENDOR_ALLOW_UNMERGED"
JSONSCHEMA_RANGE = (
    ">=4.26.0,<5"  # the range the kit is meant to run on; the kit itself is tested on the exact pin
)
sys.path.insert(0, str(KIT))

from hadavin_kit import kitsum, paths, versioning


def pins() -> dict[str, str]:
    """The dependency pins the runtime needs, read from the repository's pyproject (documented minimums)."""
    path = KIT / "pyproject.toml"
    if not path.is_file():
        raise RuntimeError(f"cannot read the dependency pins: {path} does not exist")
    text = path.read_text(encoding="utf-8")
    out = {}
    for name in ("hadavin-sdk", "jsonschema"):
        m = re.search(rf'"{name}\s*(?:@\s*[^"]*|[=<>~!][^"]*)?"', text)
        if not m:
            raise RuntimeError(f"cannot read the dependency pins: {name} is not in {path}")
        out[name] = m.group(0).strip('"')
    out["jsonschema_range"] = JSONSCHEMA_RANGE
    return out


def _git(*args: str) -> str | None:
    try:
        res = subprocess.run(  # noqa: S603 (fixed arguments)
            ["git", "-C", str(KIT), *args],  # noqa: S607 (git on PATH, fixed arguments)
            capture_output=True, text=True, check=False,
        )  # fmt: skip
    except OSError:
        return None  # git is missing: refuse rather than vendor an unverifiable commit
    return res.stdout if res.returncode == 0 else None


def merged(commit: str) -> bool:
    if not (KIT / ".git").exists():
        return False  # not a git checkout: nothing can be verified, so refuse (fail closed)
    return _git("merge-base", "--is-ancestor", commit, "origin/main") is not None


def source_problem(commit: str) -> str | None:
    """Why the files in this checkout are not the bytes of `commit` (None when they are and the commit is merged)."""
    if not (KIT / ".git").exists():
        return "this is not a git checkout: the commit cannot be verified"
    head = _git("rev-parse", "HEAD")
    if head is None or head.strip() != commit:
        return "the checkout's HEAD is not --commit: check out the merged commit first"
    dirty = _git("status", "--porcelain", "--untracked-files=all", "--", ".")
    if dirty is None or dirty.strip():
        return (
            "the kit has uncommitted or untracked changes: the copy would not be the commit's bytes"
        )
    if not merged(commit):
        return "that commit is not on origin/main; vendor only from a merged commit"
    return None


def cmd_copy(demo: Path, commit: str) -> int:
    """Copy the runtime files into DEMO/vendor/portal-kit and write DEMO/vendor/KIT.lock (the format the demos' sync check verifies)."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        print("vendor: --commit must be a full 40-hex commit id")
        return 2
    if os.environ.get(ALLOW_ENV) != "1":  # tests only; never a command-line flag
        why = source_problem(commit)
        if why:
            print(f"vendor: {why}")
            return 2
    try:
        required = pins()
    except RuntimeError as err:
        print(f"vendor: {err}")
        return 2
    # Everything below lands inside the demo: safe_join returns the REAL path, so a link or a Windows junction
    # (is_symlink() is False for a junction) at DEMO/vendor, DEMO/vendor/portal-kit or KIT.lock is refused.
    real_demo = Path(os.path.realpath(demo))
    try:
        vdir = paths.safe_join(demo, VENDOR_DIR)
        lock_path = paths.safe_join(demo, "vendor/KIT.lock")
        linked = vdir != real_demo / VENDOR_DIR or lock_path != real_demo / "vendor" / "KIT.lock"
    except paths.UnsafePath:
        linked = True
    if linked:
        print("vendor: DEMO/vendor, DEMO/vendor/portal-kit and KIT.lock must not be links")
        return 2
    files = kitsum.digests(KIT)  # hashes of the SOURCE files, taken before anything is copied
    if vdir.exists():
        shutil.rmtree(vdir)  # no stale or planted file survives a re-vendor
    for rel in files:
        target = paths.safe_join(vdir, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT / rel, target)
    lock = {"kit_commit": commit, "vendor_dir": VENDOR_DIR, "format": kitsum.LOCK_FORMAT,
            "schema_minor_max": versioning.MAX_MINOR, "requires": required,
            "files": files, "tree_sha256": kitsum.tree_hash(files)}  # fmt: skip
    paths.write_text_in(
        demo, "vendor/KIT.lock", json.dumps(lock, indent=1, sort_keys=True) + "\n", newline="\n"
    )
    problems = kitsum.check(vdir, lock_path)
    if problems:
        print("vendor: the copy does not match the source:\n  " + "\n  ".join(problems))
        return 1
    print(f"vendored {len(files)} files into {vdir}, tree {lock['tree_sha256']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    c = sub.add_parser("copy")
    c.add_argument("dest", type=Path)
    c.add_argument("--commit", required=True)
    v = sub.add_parser("verify")
    v.add_argument("dest", type=Path)
    args = ap.parse_args(argv)
    if args.cmd in ("copy", "verify"):
        try:
            args.dest = paths.cli_path(
                args.dest
            )  # command-line paths stay inside the current directory
        except ValueError as err:  # UnsafePath
            print(f"vendor: {err}")
            print(
                "vendor: run it from inside the demo repository and name the demo as a relative path, e.g. "
                "`cd <demo>; python ../portal-kit/tools/vendor.py copy . --commit <40-hex sha>`"
            )
            return 2
    if args.cmd == "manifest":
        (KIT / "KIT-SHA256SUMS").write_text(
            kitsum.sums_text(kitsum.digests(KIT)), encoding="utf-8", newline="\n"
        )
        print(KIT / "KIT-SHA256SUMS")
        return 0
    if args.cmd == "copy":
        return cmd_copy(args.dest, args.commit)
    return kitsum.main(["--check", str(args.dest)])


if __name__ == "__main__":
    sys.exit(main())
