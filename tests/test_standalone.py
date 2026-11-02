"""Repository hygiene: no studio references in the published tree, and the runtime kit never names the sample-data folder.

The patterns are built from parts so this file does not match them itself."""

import re
import subprocess
from pathlib import Path

from hadavin_kit import kitsum

ROOT = Path(__file__).resolve().parents[1]
SEP = r"[\\/]"
LEAK = re.compile(
    "|".join(
        [
            "(?:egm|handoff|bible)" + SEP,
            "Bho" + "vix",
            "studio" + r"\s+folder",
            "bho" + "vix_tools",
            r"[A-Za-z]:" + SEP + "Users",
            "Down" + "loads" + SEP,
            "users" + r"\.noreply\.github\.com",
            "builder" + "-notes",
            "kit" + "-builder",
        ]
    ),
    re.IGNORECASE,
)
R_NUMBER = re.compile(r"\bR[34]\d\d\b")  # rule-book numbers of the private studio
TEXT = {
    ".py",
    ".md",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".js",
    ".css",
    ".html",
    ".txt",
    ".properties",
    ".lock",
    "",
}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", "out"}

# Files that may name the sample-data folder: documentation, tooling, CI, the test helpers that load samples. The RUNTIME
# files (the set a demo vendors) must never name it, so product code cannot reach sample data except through a
# config-selected loader that the demo supplies.
MAY_NAME_DEMO_ONLY = {
    "README.md", "CONTRACT.md", "CHANGELOG.md", "HOSTING.md", "CORE-COPIES.md", "SECURITY.md",
    ".gitattributes", ".gitleaks.toml", ".sonarcloud.properties", ".github/workflows/ci.yml",
    "hadavin_kit/testing.py", "hadavin_kit/docgen.py",
    "tools/check_fixture.py", "tools/freeze.py", "tools/mutation.py", "tools/vendor.py",
}  # fmt: skip


def tracked_text_files():
    files = [
        p
        for p in ROOT.rglob("*")
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.name != "uv.lock"
    ]
    return [p for p in files if p.suffix in TEXT]


def test_no_studio_references_in_any_file():
    hits = []
    for p in tracked_text_files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for rx in (LEAK, R_NUMBER):
            if rx.search(text):
                hits.append(p.relative_to(ROOT).as_posix())
                break
    assert hits == [], hits


def test_runtime_files_never_name_the_sample_data_folder():
    needle = "demo" + "_only"
    runtime = kitsum.runtime_files(ROOT)
    assert runtime, "no runtime files found"
    named = [rel for rel in runtime if needle in (ROOT / rel).read_text(encoding="utf-8")]
    assert named == [], named
    assert not [rel for rel in runtime if rel.startswith(needle)]


def test_only_listed_non_runtime_files_name_the_sample_data_folder():
    needle = "demo" + "_only"
    runtime = set(kitsum.runtime_files(ROOT))
    named = set()
    for p in tracked_text_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((needle + "/", "tests/")):
            continue
        try:
            if needle in p.read_text(encoding="utf-8"):
                named.add(rel)
        except UnicodeDecodeError:
            continue
    assert named <= MAY_NAME_DEMO_ONLY, sorted(named - MAY_NAME_DEMO_ONLY)
    assert not (named & runtime), sorted(named & runtime)


def test_git_history_has_no_trailers_or_foreign_authors():
    if not (ROOT / ".git").exists():
        return
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--format=%ae|%ce"],
        capture_output=True, text=True, check=False,
    ).stdout.split()  # fmt: skip
    assert set(out) <= {"dev@hadavin.com|dev@hadavin.com"}, set(out)
