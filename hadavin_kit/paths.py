"""Where the runtime finds its data files. The kit is vendored as ONE tree with this layout:

    <root>/hadavin_kit/   the package          <root>/schema/demo-export.v1.json
    <root>/console/       the console sources

A demo's lock is NOT under <root>: tools/vendor.py writes DEMO/vendor/KIT.lock beside DEMO/vendor/portal-kit (= <root>).

`<root>` is the parent of the package directory, or `HADAVIN_KIT_ROOT` when set (a test and tooling override: the code
imported from the package can then differ from the tree that is read and hashed, so kitcheck --kit-lock reports it as a
problem). A copy that took only the package
fails with `KitLayoutError` and says what is missing, instead of with a crash deep inside a check.
"""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
OVERRIDDEN = bool(os.environ.get("HADAVIN_KIT_ROOT"))
ROOT = Path(os.environ.get("HADAVIN_KIT_ROOT") or PKG.parent).resolve()
SCHEMA = ROOT / "schema" / "demo-export.v1.json"
CONSOLE = ROOT / "console"


class KitLayoutError(RuntimeError):
    pass


def require(path: Path) -> Path:
    if not path.exists():
        raise KitLayoutError(
            f"the vendored kit is incomplete: {path} is missing. Vendor the whole tree "
            "(hadavin_kit/, schema/, console/) with tools/vendor.py copy, not just the package."
        )
    return path
