"""Run kitcheck on one example export as a demo's CI would on its own output (used by the CI matrix).

    python tools/check_fixture.py SLUG [--compat MINOR]

Builds export.json, index.html and _headers for the fixture in a temporary directory and exits non-zero on any problem.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT / "console"))

import build_console
from hadavin_kit import kitcheck


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug")
    ap.add_argument(
        "--compat",
        type=int,
        help="check the frozen compat-v1.N copy instead of the current example",
    )
    args = ap.parse_args(argv)
    src = (
        KIT
        / ("examples" if args.compat is None else f"demo_only/fixtures/compat-v1.{args.compat}")
        / f"{args.slug}.json"
    )
    doc = json.loads(src.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "export.json").write_text(json.dumps(doc), encoding="utf-8")
        (root / "index.html").write_text(build_console.build(doc), encoding="utf-8")
        _, css, js = build_console.assets()
        (root / "_headers").write_text(
            build_console.headers_file(build_console.csp_for(js, css)), encoding="utf-8"
        )
        res = kitcheck.run(root, demo=args.slug)
    for p in res["problems"]:
        print(p)
    print(
        f"{args.slug}{'' if args.compat is None else f' (compat {args.compat})'}: {'OK' if res['ok'] else 'PROBLEMS'}"
    )
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
