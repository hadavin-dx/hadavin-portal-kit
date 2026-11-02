"""Docs and code agree: every count in CONTRACT.md is generated, every ask maps to a real feature and back."""

import re

from hadavin_kit import docgen, problems, versioning
from hadavin_kit.testing import KIT, SLUGS, example

ROWS = docgen.asks()
DOC = (KIT / "CONTRACT.md").read_text(encoding="utf-8")


def test_generated_block_is_current():
    assert docgen.BEGIN in DOC and docgen.END in DOC
    committed = DOC[DOC.index(docgen.BEGIN) : DOC.index(docgen.END) + len(docgen.END)]
    assert committed == docgen.generate(), "run: python -m hadavin_kit.docgen"


def test_no_hand_typed_counts_outside_the_generated_block():
    outside = DOC[: DOC.index(docgen.BEGIN)] + DOC[DOC.index(docgen.END) :]
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"\b\d+\s+(asks?|features?|exports?|rules?|cases?|events?|codes?|examples?|demos?|mutants?)\b",
        re.IGNORECASE,
    )
    for name, text in (("CONTRACT.md", outside), ("README.md", readme)):
        assert pattern.findall(text) == [], (name, pattern.findall(text))


def test_ask_ids_are_unique_and_complete():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids))
    for r in ROWS:
        assert set(r) == {
            "id",
            "demo",
            "ask",
            "justification",
            "status",
            "features",
            "conflict",
            "resolution",
            "where",
        }
        assert (
            r["status"] in docgen.STATUS_LABEL
            and r["ask"]
            and r["justification"]
            and r["resolution"]
        ), r["id"]
        assert r["demo"] in {*SLUGS, "kit"}


def test_every_demo_that_wrote_asks_has_rows():
    assert {r["demo"] for r in ROWS} == {*SLUGS, "kit"}


def test_added_asks_name_real_features_and_every_feature_is_asked_for():
    feats = versioning.all_features()
    covered = set()
    for r in ROWS:
        unknown = set(r["features"]) - set(feats)
        assert not unknown, (r["id"], unknown)
        if r["status"] == "added":
            assert r["features"], r["id"]
        covered |= set(r["features"])
    assert covered == set(feats), sorted(set(feats) - covered)


def test_every_conflict_row_says_how_it_was_resolved():
    for r in ROWS:
        if r["conflict"]:
            assert len(r["resolution"]) > 40, r["id"]


def test_every_feature_is_exercised_by_some_example_or_a_negative_test():
    """A feature no example uses is an untested path in the console; the test suite covers the rest explicitly."""
    used = set()
    for slug in SLUGS:
        used |= set(versioning.features_used(example(slug)))
    never = sorted(set(versioning.all_features()) - used)
    # features that only appear in states the fixtures must not show (a live measurement, a signed ed25519 decision)
    allowed_unused = {
        "approver.sig_scheme=ed25519",
    }
    assert set(never) <= allowed_unused, never


def test_readme_names_every_module_the_kit_ships():
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    for mod in (
        "validate",
        "versioning",
        "kitcheck",
        "honest",
        "docgen",
        "testing",
        "kitsum",
        "paths",
        "textnorm",
    ):
        assert (KIT / "hadavin_kit" / f"{mod}.py").is_file() and mod in readme, mod
    assert (KIT / "tools" / "mutation.py").is_file() and "mutation" in readme


def test_problem_codes_in_contract_equal_code():
    for code in problems.CODES:
        assert code in DOC, code


def test_minor_one_is_declared_draft_and_reserved_features_are_real():
    from hadavin_kit import versioning

    assert versioning.STATUS == "draft" and "DRAFT" in DOC and "Per-demo gate" in DOC
    assert set(versioning.RESERVED) <= set(versioning.all_features())
    for name in versioning.RESERVED:
        assert name in DOC  # the generated block lists them
    schema = (KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8")
    assert "RESERVED" in schema and "LABEL, not verification" in schema


def test_docs_do_not_claim_what_was_not_shown():
    for name in ("README.md", "CONTRACT.md", "HOSTING.md"):
        text = (KIT / name).read_text(encoding="utf-8").lower()
        for banned in ("accessible", "wcag", "premium", "pixel-perfect"):
            assert banned not in text, (name, banned)
    assert "hand-written" not in (KIT / "README.md").read_text(encoding="utf-8")
