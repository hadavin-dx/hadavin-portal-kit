"""Versioned migration: a minor only adds; every export that validated keeps validating (hadavin_kit.versioning)."""

import copy
import hashlib
import json

import pytest
from hadavin_kit import chain, strict_json, testing, validate, versioning
from hadavin_kit.testing import KIT, SLUGS, example

COMPAT = KIT / "demo_only" / "fixtures" / "compat-v1.0"
try:
    OLD_SCHEMA = json.loads((COMPAT / "demo-export.v1.0.schema.json").read_text(encoding="utf-8"))
except OSError:  # the sample-data folder is deleted (cut test): any use of it skips the test

    class _Missing(dict):
        def _gone(self, *a, **k):
            raise FileNotFoundError("demo_only sample data is missing")

        get = __getitem__ = __iter__ = __contains__ = items = keys = values = _gone

    OLD_SCHEMA = _Missing()
NEW_SCHEMA = validate.schema()


def test_frozen_copies_are_untouched():
    """The minor-0 exports and schema are frozen evidence of what was published first."""
    listed = dict(
        reversed(line.split("  ", 1)) for line in (COMPAT / "SHA256SUMS").read_text().splitlines()
    )
    assert set(listed) == {p.name for p in COMPAT.glob("*.json")}
    for name, digest in listed.items():
        assert hashlib.sha256((COMPAT / name).read_bytes()).hexdigest() == digest, name


@pytest.mark.parametrize("slug", [*SLUGS, "green-testdouble"])
@pytest.mark.needs_samples
def test_every_minor_zero_export_still_validates(slug):
    path = COMPAT / f"{slug}.json"
    doc, problems = validate.load_and_validate(path)
    assert problems == [], [str(p) for p in problems[:3]]
    assert "schema_minor" not in doc and doc["audit"]["chain"] == "hash-chain/1"
    assert chain.verify(doc["audit"]["events"])[
        "ok"
    ]  # old pinned head hashes still verify under the old recipe


def test_schema_change_is_purely_additive():
    assert versioning.additive_diff(OLD_SCHEMA, NEW_SCHEMA, versioning.ACCEPTED_TIGHTENINGS) == []


def test_additive_diff_catches_a_real_break():
    broken = copy.deepcopy(NEW_SCHEMA)
    del broken["$defs"]["case"]["properties"]["bucket"]
    broken["$defs"]["case"]["properties"]["outcome"]["enum"].remove("blocked")
    broken["$defs"]["case"]["required"].append("terminal_state")
    broken["$defs"]["step"]["properties"]["ms"]["type"] = "string"
    got = " | ".join(versioning.additive_diff(OLD_SCHEMA, broken))
    assert "property removed" in got and "enum values removed" in got
    assert "new required keys" in got and "type narrowed" in got


def _props(schema, ctx):
    d = schema["$defs"]
    node = {
        "root": schema,
        "case": d["case"],
        "expected": d["case"]["properties"]["expected"],
        "approval": d["approval"],
        "approver": d["approval"]["properties"]["approvers"]["items"],
        "adapter_call": d["adapter_call"],
        "audit_event": d["audit_event"],
        "scorecard": d["scorecard"],
        "run": schema["properties"]["run"],
    }[ctx]
    return set(node["properties"])


def test_keys_added_table_equals_the_real_schema_difference():
    declared = {
        (c, k) for by_ctx in versioning.KEYS_ADDED.values() for c, ks in by_ctx.items() for k in ks
    }
    actual = {
        (ctx, k)
        for ctx in {c for c, _ in declared}
        for k in _props(NEW_SCHEMA, ctx) - _props(OLD_SCHEMA, ctx)
    }
    assert declared == actual


def test_enums_added_table_equals_the_real_schema_difference():
    def enum(schema, ctx, key):
        d = schema["$defs"]
        node = {
            "case": d["case"]["properties"],
            "expected": d["case"]["properties"]["expected"]["properties"],
            "approval": d["approval"]["properties"],
            "adapter_call": d["adapter_call"]["properties"],
            "audit_event": d["audit_event"]["properties"],
            "audit": schema["properties"]["audit"]["properties"],
        }[ctx][key]
        if "$ref" in node:
            node = d[node["$ref"].rsplit("/", 1)[1]]
        return set(map(str, node["enum"] if "enum" in node else [node["const"]]))

    declared = {
        (c, k): set(v) for by_key in versioning.ENUMS_ADDED.values() for (c, k), v in by_key.items()
    }
    for (ctx, key), values in declared.items():
        diff = enum(NEW_SCHEMA, ctx, key) - enum(OLD_SCHEMA, ctx, key)
        assert diff == values, (ctx, key, diff, values)


@pytest.mark.parametrize("slug", SLUGS)
def test_a_new_feature_needs_its_minor(slug):
    d = example(slug)
    used = versioning.features_used(d)
    assert used and set(used.values()) == {1}
    d["schema_minor"] = 0  # same bytes, understated minor
    got = validate.validate(d)
    low = {p for p in got if p.code == "MINOR_TOO_LOW"}
    assert len(low) >= len(used) - 1  # every used feature except schema_minor itself is named
    d2 = testing.compat(slug)
    d2["tenant_id"] = "x"
    assert any(
        p.code == "MINOR_TOO_LOW" and "root.tenant_id" in p.message for p in validate.validate(d2)
    )


def test_unknown_future_minor_is_refused_not_guessed():
    d = example("refunds")
    d["schema_minor"] = 2
    codes = {p.code for p in validate.validate(d)}
    assert "UNSUPPORTED_MINOR" in codes and "BAD_VALUE" in codes
    for bad in (-1, "1", 1.5, True, None):
        d["schema_minor"] = bad
        assert validate.validate(d)  # never valid


def test_old_reader_would_reject_new_keys_so_the_minor_gate_matters():
    """A strict minor-0 reader (additionalProperties false) rejects the new keys: the declared minor tells it why."""
    from jsonschema import Draft202012Validator

    errs = list(Draft202012Validator(OLD_SCHEMA).iter_errors(example("refunds")))
    assert errs and example("refunds")["schema_minor"] == 1


def test_migrate_never_invents_data():
    old = testing.compat("refunds")
    up, open_problems = versioning.migrate(old)
    assert up == old and up.get("schema_minor", 0) == 0
    assert open_problems and "re-export" in open_problems[0]
    new, none_open = versioning.migrate(example("refunds"))
    assert none_open == [] and new == example("refunds")


def test_every_example_is_the_latest_minor_and_old_copies_are_minor_zero():
    for slug in SLUGS:
        assert example(slug)["schema_minor"] == versioning.MAX_MINOR
        assert "schema_minor" not in testing.compat(slug)


def test_strict_loader_still_rejects_what_the_browser_would_disagree_on(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key"):
        strict_json.load_object(p)


def test_schema_event_types_equal_the_chain_writer_and_versioning_tables():
    enum = NEW_SCHEMA["$defs"]["audit_event"]["properties"]["type"]["enum"]
    assert set(enum) == set(chain.EVENT_TYPES)
    assert set(chain.EVENT_TYPES_V1_ADDED) == set(
        versioning.ENUMS_ADDED[1][("audit_event", "type")]
    )
    w = chain.ChainWriter()  # hash-chain/1 writer must refuse the minor-1 event types
    with pytest.raises(ValueError, match="hash-chain/2"):
        w.append(ts="2026-11-01T09:00:00Z", type="hold", actor="system", case_id="", summary="x")
    w2 = chain.ChainWriter(tenant_id="t")
    assert (
        w2.append(ts="2026-11-01T09:00:00Z", type="hold", actor="system", case_id="", summary="x")[
            "tenant_id"
        ]
        == "t"
    )
    assert w2.audit_block()["chain"] == chain.CHAIN_2
