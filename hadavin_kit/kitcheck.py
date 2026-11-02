"""`kitcheck`: the conformance check every demo runs on its export directory.

    python -m hadavin_kit.kitcheck <export-dir> [--demo SLUG] [--deny-list FILE] [--require-report] [--json]

Exit code 0 = conforms, 1 = problems found, 2 = the directory could not be read at all.

The directory holds `export.json` (required) and optionally the built console (`index.html` + `_headers`) and the raw
load report (`report.json`). It checks, in this order, and reports every problem with a typed code:
1. file hygiene: only the names above, no symlinks, no file over 16 MB, no weights/index/database/archive types;
2. `export.json`: strict JSON, schema, minor, cross-field rules, and the audit chain recomputed (`hadavin_kit.validate`);
3. the console page, when present: the embedded data equals `export.json`, the inline script and style hashes match
   the CSP in the page and in `_headers`, no remote URL anywhere;
4. the load report, when present: its sha256 equals the one the export names;
5. the deny-list, when given (names and canaries of other clients, one per line): none may appear.
Anything it could not check is listed under `notes`; "not checked" is never reported as a pass.
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from hadavin_kit import chain, honest, kitsum, paths, strict_json, textnorm, validate, versioning
from hadavin_kit.validate import Problem

ALLOWED_NAMES = ("export.json", "index.html", "_headers", "report.json")
KIT = paths.ROOT
TEST_DOUBLE_MODEL = "test-double-not-a-model"
MAX_FILE_BYTES = 16_000_000
FORBIDDEN_SUFFIXES = (
    ".pkl", ".pickle", ".pt", ".pth", ".bin", ".safetensors", ".onnx", ".gguf", ".npy", ".npz", ".parquet",
    ".db", ".sqlite", ".sqlite3", ".faiss", ".index", ".zip", ".tar", ".gz", ".7z", ".ipynb", ".env",
)  # fmt: skip
_DATA_SCRIPT = re.compile(
    r'(<script type="application/json" id="export-data">).*?(</script>)', re.DOTALL
)
_REMOTE = re.compile(r"(?i)(https?:)?//[a-z0-9.-]+\.[a-z]{2,}|@import|url\(")


def _b64_sha256(text: str) -> str:
    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


class _Page(HTMLParser):
    """Collects the CSP meta, inline script/style bodies and the embedded export data of a built console."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.csps: list[str] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self.data: str | None = None
        self._tag: str | None = None
        self._is_data = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "meta" and (a.get("http-equiv") or "").lower() == "content-security-policy":
            self.csps.append(a.get("content") or "")
        if tag in ("script", "style"):
            self._tag, self._buf = tag, []
            self._is_data = tag == "script" and a.get("id") == "export-data"

    def handle_data(self, data: str) -> None:
        if self._tag:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._tag:
            text = "".join(self._buf)
            if self._is_data:
                self.data = text
            elif tag == "script":
                self.scripts.append(text)
            else:
                self.styles.append(text)
            self._tag = None


def _safe(text: object) -> str:
    return ascii(str(text))[1:-1]


def _strings(value: Any, depth: int = 0) -> list[str]:
    """Every string AND every key of a decoded JSON value (the raw file text hides non-ASCII as escapes)."""
    if depth > 60:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out += [str(k), *_strings(v, depth + 1)]
        return out
    if isinstance(value, list):
        return [s for v in value for s in _strings(v, depth + 1)]
    return []


@functools.cache
def _kit_console() -> Any:
    """The kit's own console builder (the page is compared with what the kit would build, not with itself)."""
    spec = importlib.util.spec_from_file_location(
        "kit_build_console", paths.require(KIT / "console" / "build_console.py")
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _hygiene(root: Path) -> list[Problem]:
    out: list[Problem] = []
    for p in sorted(root.rglob("*")):
        rel = _safe(p.relative_to(root).as_posix())
        if p.is_symlink():
            out.append(Problem("UNEXPECTED_FILE", rel, "symbolic links are not allowed"))
        elif p.is_dir():
            out.append(Problem("UNEXPECTED_FILE", rel, "sub-directories are not allowed"))
        elif p.name not in ALLOWED_NAMES or p.parent != root:
            kind = (
                "forbidden file type"
                if p.suffix.lower() in FORBIDDEN_SUFFIXES
                else "not in the export manifest"
            )
            out.append(Problem("UNEXPECTED_FILE", rel, kind))
        elif p.stat().st_size > MAX_FILE_BYTES:
            out.append(Problem("UNEXPECTED_FILE", rel, f"larger than {MAX_FILE_BYTES} bytes"))
    return out


def _canon(value: Any) -> str:
    """Type-exact canonical text: 1, 1.0 and true all differ (Python == would equate them)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def _page_problems(root: Path, doc: dict[str, Any] | None, notes: list[str]) -> list[Problem]:
    page_path, headers_path = root / "index.html", root / "_headers"
    if not page_path.exists():
        notes.append("no index.html: console page not checked")
        return []
    out: list[Problem] = []
    text = page_path.read_text(encoding="utf-8")
    page = _Page()
    page.feed(text)
    # remote references are looked for in the skeleton, scripts and styles only: the embedded data may legitimately
    # contain a URL or "url(" in a case summary
    skeleton = _DATA_SCRIPT.sub(r"\1\2", text)
    if _REMOTE.search(skeleton):
        out.append(
            Problem("PAGE_MISMATCH", "index.html", "page references a remote URL, @import or url()")
        )
    if len(page.csps) != 1 or len(page.scripts) != 1 or len(page.styles) != 1 or page.data is None:
        return [
            *out,
            Problem(
                "PAGE_MISMATCH",
                "index.html",
                "not a console page (exactly one CSP meta, one script, one style, data)",
            ),
        ]
    csp, js, css = page.csps[0], page.scripts[0], page.styles[0]
    _, css_kit, js_kit = _kit_console().assets()
    expected_csp = _kit_console().csp_for(js_kit, css_kit)
    if f"script-src {_b64_sha256(js)}" not in csp or f"style-src {_b64_sha256(css)}" not in csp:
        out.append(
            Problem(
                "PAGE_MISMATCH", "index.html", "inline script or style hash is not in the page CSP"
            )
        )
    if "unsafe-inline" in csp or "unsafe-eval" in csp:
        out.append(
            Problem("PAGE_MISMATCH", "index.html", "CSP allows unsafe-inline or unsafe-eval")
        )
    if csp != expected_csp:
        out.append(
            Problem(
                "PAGE_MISMATCH", "index.html", "CSP differs from the kit's CSP (exact comparison)"
            )
        )
    if js != js_kit or css != css_kit:
        out.append(
            Problem(
                "PAGE_MISMATCH",
                "index.html",
                "inline script or style differs from the kit's console",
            )
        )
    try:
        embedded = strict_json.loads(page.data)
    except ValueError as err:
        embedded = None
        out.append(
            Problem("PAGE_MISMATCH", "index.html", f"embedded data is not strict JSON: {err}")
        )
    if embedded is not None and doc is not None and _canon(embedded) != _canon(doc):
        out.append(Problem("PAGE_MISMATCH", "index.html", "embedded data differs from export.json"))
    if doc is not None:
        try:
            built = _kit_console().build(doc, rules=False)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError) as err:
            built = None
            out.append(
                Problem(
                    "PAGE_MISMATCH",
                    "index.html",
                    f"the kit cannot build a page from this export.json ({type(err).__name__}): the document is malformed",
                )
            )
        if built is not None and text.replace("\r\n", "\n") != built.replace("\r\n", "\n"):
            out.append(
                Problem(
                    "PAGE_MISMATCH",
                    "index.html",
                    "page differs from the page the kit builds from export.json (skeleton, banner, navigation or text edited)",
                )
            )
    if not headers_path.exists():
        out.append(
            Problem("MISSING_FILE", "_headers", "index.html needs the matching _headers file")
        )
    else:
        have = headers_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if have != _kit_console().headers_file(expected_csp):
            out.append(
                Problem(
                    "PAGE_MISMATCH",
                    "_headers",
                    "headers differ from the kit's headers (exact comparison)",
                )
            )
    return out


def _deny_problems(
    root: Path, doc: dict[str, Any] | None, deny: list[str] | None, notes: list[str]
) -> list[Problem]:
    if deny is None:
        notes.append("no deny-list given: other clients' names and canaries were not scanned")
        return []
    terms = [textnorm.fold(t.strip()) for t in deny if t.strip().lstrip("\ufeff")]
    terms = [t for t in terms if t]
    if not terms:
        notes.append(
            "deny-list given but it has no effective term (only blank lines): nothing was scanned"
        )
        return []
    texts: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.is_symlink() or p.suffix not in (".json", ".html", ".md"):
            continue
        raw = p.read_text(encoding="utf-8-sig", errors="replace")
        texts.append(raw)
        if p.name == "export.json" and doc is not None:
            texts += _strings(doc)
        elif p.suffix == ".json":
            # every .json (report.json too) is decoded: an ensure_ascii writer stores "M\u00fcller" as an escape
            try:
                texts += _strings(strict_json.loads(raw))
            except (ValueError, RecursionError):
                notes.append(f"{_safe(p.name)} is not strict JSON: scanned as raw text only")
        elif p.suffix == ".html":
            page = _Page()
            page.feed(raw)
            try:
                texts += _strings(strict_json.loads(page.data or "null"))
            except (ValueError, RecursionError):
                pass
    hay = textnorm.fold("\n".join(texts))
    return [
        Problem("DENYLIST_HIT", "", f"deny-list term #{i} appears in the export")
        for i, t in enumerate(terms, start=1)
        if t in hay
    ]


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"[0-9]+", text)[:3])


def _range_note(installed: str, rng: str) -> str | None:
    """A note when the installed version is outside a `>=A,<B` range (no packaging dependency: numbers only)."""
    have = _version_tuple(installed)
    for part in rng.split(","):
        part = part.strip()
        want = _version_tuple(part)
        if (part.startswith(">=") and have < want) or (part.startswith("<") and have >= want):
            return f"jsonschema {installed} is outside the range the vendored kit declares ({rng})"
    return None


def _lock_problems(
    doc: dict[str, Any], lock_path: Path, notes: list[str] | None = None
) -> list[Problem]:
    """The export must name the vendored kit commit, and the running kit must be the files that lock describes."""
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        locked = lock["kit_commit"]
    except (OSError, ValueError, KeyError, TypeError):
        return [
            Problem(
                "KIT_LOCK", _safe(lock_path.name), "unreadable, or not a KIT.lock with a kit_commit"
            )
        ]
    out = []
    if paths.OVERRIDDEN:
        out.append(
            Problem(
                "KIT_LOCK",
                "HADAVIN_KIT_ROOT",
                "is set: the code imported from the package can differ from the tree this check hashes",
            )
        )
    rng = (lock.get("requires") or {}).get("jsonschema_range") if isinstance(lock, dict) else None
    if notes is not None and isinstance(rng, str):
        try:
            note = _range_note(importlib.metadata.version("jsonschema"), rng)
        except importlib.metadata.PackageNotFoundError:
            note = "jsonschema is not installed"
        if note:
            notes.append(note)
    claimed = (doc.get("demo") or {}).get("kit_commit")
    if not isinstance(claimed, str) or not re.fullmatch(r"[0-9a-f]{40}", claimed):
        out.append(
            Problem(
                "KIT_LOCK",
                "demo/kit_commit",
                f"{_safe(claimed)} is not a full 40-hex commit id: an uncommitted or abbreviated export cannot be tied to a vendored kit",
            )
        )
    elif claimed != locked:
        out.append(
            Problem("KIT_LOCK", "demo/kit_commit", "differs from the kit_commit in KIT.lock")
        )
    if (
        isinstance(lock.get("schema_minor_max"), int)
        and doc.get("schema_minor", 0) > lock["schema_minor_max"]
    ):
        out.append(
            Problem(
                "KIT_LOCK",
                "schema_minor",
                "the export is newer than the vendored kit's maximum minor",
            )
        )
    out += [Problem("KIT_LOCK", "KIT.lock", m) for m in kitsum.check(paths.ROOT, lock_path)]
    return out


def run(
    root: Path,
    *,
    demo: str | None = None,
    deny: list[str] | None = None,
    require_report: bool = False,
    expect_head: str | None = None,
    kit_lock: Path | None = None,
    deploy: bool = False,
) -> dict[str, Any]:
    notes: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "problems": [],
        "notes": notes,
        "minor": None,
        "honest": [],
        "chain": None,
    }
    if not root.is_dir():
        result["problems"] = [Problem("MISSING_FILE", _safe(root), "not a directory")]
        result["unreadable"] = True
        return result
    problems = _hygiene(root)
    export_path = root / "export.json"
    doc: dict[str, Any] | None = None
    if not export_path.is_file() or export_path.is_symlink():
        problems.append(Problem("MISSING_FILE", "export.json", "required"))
    else:
        doc, found = validate.load_and_validate(export_path)
        problems += found
    if doc is not None and not any(
        p.code in ("UNKNOWN_KEY", "WRONG_TYPE", "MISSING_KEY") for p in problems
    ):
        result["minor"] = doc.get("schema_minor", 0)
        try:
            result["honest"] = honest.status_ids(doc)
            ev = doc["audit"]["events"]
            result["chain"] = {
                **chain.verify(ev, doc["audit"]["chain"]),
                "head": ev[-1]["sha256"] if ev else chain.GENESIS,
            }
        except (KeyError, TypeError, ValueError):
            notes.append("honest status and chain head not computed: export is malformed")
        if expect_head is not None and (
            result["chain"] is None or result["chain"]["head"] != expect_head.strip().lower()
        ):
            problems.append(
                Problem(
                    "CHAIN",
                    "audit",
                    "chain head differs from --expect-head (truncation or re-seal?)",
                )
            )
        if deploy and expect_head is None:
            problems.append(
                Problem(
                    "CHAIN", "audit", "--deploy needs --expect-head (a chain head pinned elsewhere)"
                )
            )
        if kit_lock is not None:
            problems += _lock_problems(doc, kit_lock, notes)
        elif deploy:
            problems.append(
                Problem("KIT_LOCK", "", "--deploy needs --kit-lock (the vendored kit's KIT.lock)")
            )
        else:
            notes.append("no --kit-lock: the kit version the demo ran against was not checked")
        if expect_head is None:
            notes.append(
                "no --expect-head: a truncated or fully re-sealed chain would still verify; pin the head elsewhere"
            )
        if demo is not None and doc.get("demo", {}).get("slug") != demo:
            problems.append(
                Problem(
                    "RULE",
                    "demo/slug",
                    f"expected demo {_safe(demo)}, found {_safe(doc.get('demo', {}).get('slug'))}",
                )
            )
        notes_0 = (doc.get("honesty") or {}).get("notes") or [""]
        if (doc.get("run") or {}).get("model") == TEST_DOUBLE_MODEL or str(notes_0[0]).startswith(
            "TEST DOUBLE"
        ):
            problems.append(
                Problem(
                    "RULE",
                    "run/model",
                    "the green-state test double is a rendering fixture, never for a deploy directory",
                )
            )
        for key in ("ledger", "load_profile"):
            want = (doc.get(key) or {}).get("report_sha256")
            rp = root / "report.json"
            if want is None:
                continue
            if rp.is_file():
                if hashlib.sha256(rp.read_bytes()).hexdigest() != want:
                    problems.append(
                        Problem(
                            "REPORT_MISMATCH",
                            f"{key}/report_sha256",
                            "does not equal the sha256 of report.json",
                        )
                    )
                else:
                    notes.append(
                        f"{key}: report.json matches by sha256 only; its contents were not compared with the ledger counts"
                    )
            elif require_report:
                problems.append(
                    Problem(
                        "MISSING_FILE",
                        "report.json",
                        f"{key} names a raw report that is not in the directory",
                    )
                )
            else:
                notes.append(
                    f"{key}.report_sha256 not verified: report.json is not in the directory"
                )
    rp = root / "report.json"
    if rp.is_file() and not rp.is_symlink():
        try:
            strict_json.loads(rp.read_text(encoding="utf-8-sig"))
        except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
            if require_report or deploy:
                problems.append(
                    Problem(
                        "REPORT_MISMATCH",
                        "report.json",
                        "is not strict JSON (duplicate key, NaN, too deep or not UTF-8): it cannot be scanned reliably",
                    )
                )
            else:
                notes.append(
                    "report.json is not strict JSON: the deny-list scan reads it as raw text only"
                )
    problems += _page_problems(root, doc, notes)
    problems += _deny_problems(root, doc, deny, notes)
    result["problems"], result["ok"] = problems, not problems
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kitcheck", description=__doc__.splitlines()[0])
    ap.add_argument("directory", type=Path)
    ap.add_argument("--demo", help="expected demo slug")
    ap.add_argument(
        "--deny-list", type=Path, help="file of terms (one per line) that must not appear"
    )
    ap.add_argument(
        "--require-report", action="store_true", help="fail when a named raw report is missing"
    )
    ap.add_argument(
        "--expect-head", help="chain head hash pinned elsewhere; a different head is a problem"
    )
    ap.add_argument(
        "--kit-lock", type=Path, help="the demo's KIT.lock: the export's kit_commit must match it"
    )
    ap.add_argument(
        "--deploy",
        action="store_true",
        help="deploy mode: needs --expect-head and --kit-lock, and the raw report",
    )
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    deny = None
    if args.deny_list:
        deny = args.deny_list.read_text(encoding="utf-8-sig").splitlines()
    try:
        res = run(
            args.directory,
            demo=args.demo,
            deny=deny,
            require_report=args.require_report or args.deploy,
            expect_head=args.expect_head,
            kit_lock=args.kit_lock,
            deploy=args.deploy,
        )
    except paths.KitLayoutError as err:
        print(f"kitcheck: {err}")
        return 2
    if args.json:
        print(json.dumps({**res, "problems": [p.as_dict() for p in res["problems"]]}, indent=2))
    else:
        print(
            f"kitcheck {args.directory}: {'OK' if res['ok'] else 'PROBLEMS'} (schema minor {res['minor']}, max supported {versioning.MAX_MINOR})"
        )
        for p in res["problems"]:
            print(f"  {p}")
        for n in res["notes"]:
            print(f"  note: {n}")
        if res["chain"]:
            print(f"  chain: {res['chain']['text']}; head {res['chain']['head']}")
        if res["honest"]:
            print(f"  honest status: {', '.join(res['honest'])}")
    return 2 if res.get("unreadable") else (0 if res["ok"] else 1)


if __name__ == "__main__":
    sys.exit(main())
