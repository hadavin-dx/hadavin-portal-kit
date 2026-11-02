"""Freeze the current exports and schema as `demo_only/fixtures/compat-v1.<N>/` when minor N leaves draft.

    python tools/freeze.py MINOR

Run it when the first real demo exporter passes `kitcheck` at that minor, and BEFORE any later minor is merged. The
frozen set (schema, five example exports, the green test double, SHA256SUMS) is evidence of what was promised:
tests diff every later schema against it, and against the frozen set of minor 0, so a new minor cannot narrow what an
earlier one accepted. A frozen directory is never edited; to change it, publish a new schema string.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def freeze(kit: Path, minor: int) -> Path:
    dest = kit / "demo_only" / "fixtures" / f"compat-v1.{minor}"
    if dest.exists():
        raise SystemExit(f"{dest} already exists: a frozen minor is never rewritten")
    dest.mkdir(parents=True)
    names = []
    for src in sorted((kit / "demo_only" / "examples").glob("*.json")):
        shutil.copyfile(src, dest / src.name)
        names.append(src.name)
    shutil.copyfile(
        kit / "demo_only" / "fixtures" / "green-testdouble.json", dest / "green-testdouble.json"
    )
    names.append("green-testdouble.json")
    schema_name = f"demo-export.v1.{minor}.schema.json"
    shutil.copyfile(kit / "schema" / "demo-export.v1.json", dest / schema_name)
    names.append(schema_name)
    sums = [f"{hashlib.sha256((dest / n).read_bytes()).hexdigest()}  {n}" for n in sorted(names)]
    (dest / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    return dest


def frozen_dirs(kit: Path) -> dict[int, Path]:
    out = {}
    for p in (kit / "demo_only" / "fixtures").glob("compat-v1.*"):
        if p.is_dir() and p.name.split(".")[-1].isdigit():
            out[int(p.name.split(".")[-1])] = p
    return dict(sorted(out.items()))


if __name__ == "__main__":
    print(freeze(KIT, int(sys.argv[1])))
