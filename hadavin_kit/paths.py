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
from pathlib import Path, PurePosixPath, PureWindowsPath

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


_WINDOWS_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class UnsafePath(ValueError):
    """A path that is absolute, uses `..`, names a drive or leaves its base directory (also through a link)."""


def unsafe_reason(rel: str) -> str | None:
    """Why `rel` is not a plain relative path (None = fine). Checked in BOTH path flavours, so a Windows form
    (a drive such as `C:x`, a network share, a backslash before `..`) is refused on every platform: the same text must
    mean the same everywhere. Also refused: a name ending in a dot or space and the Windows device names. Not refused
    here, but caught by `inside` (it follows the real path): 8.3 short names such as `PROGRA~1`."""
    if not isinstance(rel, str) or not rel:
        return "empty or not text"
    if "\0" in rel:
        return "contains a NUL character"
    if PureWindowsPath(rel).drive or PureWindowsPath(rel).root or PurePosixPath(rel).is_absolute():
        return "absolute, or names a drive"
    if ":" in rel:
        return "contains a colon (drive letter or stream name)"
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts:
        return "contains .."
    for part in parts:
        if part in (".", ""):  # the current directory and a doubled slash name nothing odd
            continue
        if part != part.rstrip(" ."):
            return (
                "a name ends with a dot or a space (Windows drops it, so it names something else)"
            )
        if part.split(".")[0].upper() in _WINDOWS_DEVICES:
            return "a Windows device name (CON, NUL, COM1, ...)"
    return None


def confine(base: Path | str, candidate: Path | str) -> Path:
    """The real path of `candidate` (every link followed), refused (UnsafePath) unless it lies within the real path of
    `base`. The check is `startswith` on real paths, the pattern static analysers recognise as a path guard."""
    real_base = os.path.realpath(base)
    real = os.path.realpath(candidate)
    inside_base = real == real_base or real.startswith(real_base.rstrip(os.sep) + os.sep)
    if not inside_base:
        raise UnsafePath("unsafe path: leaves the base directory (also through a link)")
    return Path(real)


def inside(base: Path | str, candidate: Path | str) -> bool:
    """True when `candidate`, after following every link, lies within `base` (also after following links)."""
    try:
        confine(base, candidate)
    except UnsafePath:
        return False
    return True


def safe_join(base: Path | str, rel: str) -> Path:
    """`base/rel`, refused (UnsafePath) when `rel` is absolute, has `..` or a drive, or when the result leaves `base`
    through a symlink or junction. THE one place every path that comes from a file's content is joined.
    Limit: this is check-then-open. A link swapped in between this check and the caller's open is not caught; the
    protection is against a path that is already wrong, not against a second process racing the check."""
    why = unsafe_reason(rel)
    if why:
        raise UnsafePath(f"unsafe path {rel!r}: {why}")
    try:
        return confine(base, Path(base) / rel)
    except UnsafePath:
        raise UnsafePath(f"unsafe path {rel!r}: leaves the base directory through a link") from None


def cli_path(arg: Path | str, base: Path | str | None = None) -> Path:
    """A path typed on a command line, confined to `base` (default: the current directory). A relative path is
    joined by `safe_join`; an absolute one is accepted only when it already lies inside `base`."""
    base = Path(base) if base is not None else Path.cwd()
    text = str(arg)
    if "\0" in text:
        raise UnsafePath("unsafe path: contains a NUL character")
    p = Path(text)
    if p.is_absolute():
        if ":" in p.name:
            raise UnsafePath(
                f"unsafe path {text!r}: the file name contains a colon (a Windows alternate data stream)"
            )
        if not inside(base, p):
            raise UnsafePath(
                f"unsafe path {text!r}: outside the current directory. Change into the folder that holds it "
                "(cd <folder>) and give a path relative to it."
            )
        return confine(base, p)
    try:
        return safe_join(base, text)
    except UnsafePath as err:
        raise UnsafePath(
            f"{err}. A command-line path must stay inside the current directory: cd into the folder that holds it."
        ) from None


def read_text_in(base: Path | str, rel: str, encoding: str = "utf-8") -> str:
    return safe_join(base, rel).read_text(encoding=encoding)


def write_text_in(
    base: Path | str, rel: str, text: str, encoding: str = "utf-8", newline: str | None = None
) -> None:
    safe_join(base, rel).write_text(text, encoding=encoding, newline=newline)


def read_bytes_in(base: Path | str, rel: str) -> bytes:
    return safe_join(base, rel).read_bytes()
