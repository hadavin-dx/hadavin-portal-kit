"""Boundaries and branches that the mutation harness showed no earlier test pinned down.

Each test names what a plausible one-character bug would break (an off-by-one, an `or` for an `and`, a deleted branch).
"""

import copy
import hashlib
import json
import sys

import pytest
from hadavin_kit import chain, export_rules, honest, kitcheck, validate, versioning
from hadavin_kit.problems import Typed
from hadavin_kit.testing import KIT, SLUGS, example

sys.path.insert(0, str(KIT / "console"))
import build_console


# ------------------------------------------------------------------------------------------- limits
def nested(n):
    d = {}
    cur = d
    for _ in range(n):
        cur["n"] = {}
        cur = cur["n"]
    return d


def test_nesting_limit_is_exactly_max_depth():
    d = example("refunds")
    # args sits at depth 3 (root, approvals list, approval); n levels inside it reach depth 3 + n
    ok_levels = export_rules.MAX_DEPTH - 3
    d["approvals"][0]["args"]["x"] = nested(ok_levels - 1)
    assert not [m for m in export_rules.check(d) if "nested deeper" in m]
    d["approvals"][0]["args"]["x"] = nested(ok_levels)
    assert any("nested deeper than" in m for m in export_rules.check(d))


def test_money_key_search_gives_up_exactly_past_max_depth():
    assert export_rules._has_money_key(nested(export_rules.MAX_DEPTH)) is False
    assert export_rules._has_money_key(nested(export_rules.MAX_DEPTH + 1)) is True


def test_string_limit_is_exactly_max_string():
    d = example("refunds")
    d["approvals"][0]["args"]["note"] = "y" * export_rules.MAX_STRING
    assert not [m for m in export_rules.check(d) if "string longer" in m]
    d["approvals"][0]["args"]["note"] = "y" * (export_rules.MAX_STRING + 1)
    assert any("string longer than" in m for m in export_rules.check(d))


def test_check_itself_enforces_the_document_size_limit(monkeypatch):
    monkeypatch.setattr(export_rules, "MAX_DOC_BYTES", 1000)
    got = export_rules.check(example("refunds"))
    assert any(getattr(m, "code", "") == "OVERSIZE" and "bytes" in m for m in got)


def test_list_limits_are_checked_before_the_slow_schema_walk():
    d = example("refunds")
    d["cases"] = [
        None
    ] * 2001  # items are invalid on purpose: the walk over them must not even start
    got = validate.validate(d)
    assert [p.code for p in got] == ["OVERSIZE"] and "the limit is 2000" in got[0].message
    assert validate._precheck({"cases": [None] * 2000}) == []
    assert len(validate._precheck({"cases": [None] * 2001})) == 1
    assert len(validate._precheck({"audit": {"events": [None] * 20001}})) == 1
    assert validate._precheck({"audit": {"events": [None] * 20000}}) == []


def test_problem_string_form():
    assert str(validate.Problem("RULE", "", "x")) == "RULE <root>: x"
    assert str(validate.Problem("RULE", "a/b", "x")) == "RULE a/b: x"


# ------------------------------------------------------------------------------------------- versioning
FRAGMENTS = {
    "root": lambda k: {k: 1},
    "case": lambda k: {"cases": [{k: 1}]},
    "expected": lambda k: {"cases": [{"expected": {k: 1}}]},
    "approval": lambda k: {"approvals": [{k: 1}]},
    "approver": lambda k: {"approvals": [{"approvers": [{k: 1}]}]},
    "adapter_call": lambda k: {"adapter_calls": [{k: 1}]},
    "audit_event": lambda k: {"audit": {"events": [{k: 1}]}},
    "scorecard": lambda k: {"scorecard": {k: 1}},
    "run": lambda k: {"run": {k: 1}},
}
ENUM_FRAGMENTS = {
    "case": lambda k, v: {"cases": [{k: v}]},
    "expected": lambda k, v: {"cases": [{"expected": {k: v}}]},
    "approval": lambda k, v: {"approvals": [{k: v}]},
    "adapter_call": lambda k, v: {"adapter_calls": [{k: v}]},
    "audit_event": lambda k, v: {"audit": {"events": [{k: v}]}},
    "audit": lambda k, v: {"audit": {k: v}},
}


def test_every_new_key_is_detected_in_its_own_context_and_only_there():
    for minor, by_ctx in versioning.KEYS_ADDED.items():
        for ctx, keys in by_ctx.items():
            for key in keys:
                found = versioning.features_used(FRAGMENTS[ctx](key))
                assert found == {f"{ctx}.{key}": minor}, (ctx, key, found)
                for other, make in FRAGMENTS.items():
                    if other != ctx and key not in by_ctx.get(other, ()):
                        assert f"{ctx}.{key}" not in versioning.features_used(make(key)), (
                            ctx,
                            other,
                            key,
                        )


def test_every_new_enum_value_is_detected_and_old_values_are_not():
    for minor, by_key in versioning.ENUMS_ADDED.items():
        for (ctx, key), values in by_key.items():
            for v in values:
                assert versioning.features_used(ENUM_FRAGMENTS[ctx](key, v)) == {
                    f"{ctx}.{key}={v}": minor
                }
            assert versioning.features_used(ENUM_FRAGMENTS[ctx](key, "an-old-value")) == {}


def test_malformed_documents_do_not_crash_feature_detection():
    for doc in (
        {},
        {"cases": None},
        {"cases": [1, "x", None]},
        {"audit": 5},
        {"approvals": [{"approvers": 3}]},
    ):
        assert versioning.features_used(doc) == {}


def test_additive_diff_details():
    base = json.loads((KIT / "schema" / "demo-export.v1.json").read_text(encoding="utf-8"))
    old = copy.deepcopy(base)
    assert versioning.additive_diff(old, base) == []

    def broken(fn):
        new = copy.deepcopy(base)
        fn(new)
        return " | ".join(versioning.additive_diff(old, new))

    # a const may not change
    old_c = _with(
        base,
        lambda s: s["properties"]["audit"]["properties"].update(chain={"const": "hash-chain/1"}),
    )
    new_c = _with(
        base, lambda s: s["properties"]["audit"]["properties"].update(chain={"const": "x"})
    )
    assert "const changed" in " ".join(versioning.additive_diff(old_c, new_c))
    assert versioning.additive_diff(old_c, old_c) == []
    # a type list in another order is the same set: not a break
    assert (
        versioning.additive_diff(
            old,
            _with(
                base,
                lambda s: s["$defs"]["case"]["properties"]["approval_id"].update(
                    type=["null", "string"]
                ),
            ),
        )
        == []
    )
    # narrowing a nullable to a plain type is a break
    assert "type narrowed" in broken(
        lambda s: s["$defs"]["case"]["properties"]["approval_id"].update(type="string")
    )
    # properties removed inside array items and inside additionalProperties are found too
    assert "property removed" in broken(
        lambda s: s["$defs"]["approval"]["properties"]["approvers"]["items"]["properties"].pop(
            "sig_ok"
        )
    )
    assert "property removed" in broken(
        lambda s: s["$defs"]["scorecard"]["properties"]["confident_wrong"]["properties"].pop(
            "items"
        )
    )
    old2 = copy.deepcopy(base)
    old2["$defs"]["scorecard"]["properties"]["hard_zeros"]["additionalProperties"] = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
    }
    new2 = copy.deepcopy(old2)
    new2["$defs"]["scorecard"]["properties"]["hard_zeros"]["additionalProperties"][
        "properties"
    ] = {}
    assert "property removed" in " ".join(versioning.additive_diff(old2, new2))


def _with(schema, fn):
    out = copy.deepcopy(schema)
    fn(out)
    return out


def test_declared_minor_rejects_every_unusable_value():
    for bad in (-1, 2, "1", 1.0, True, None, [1]):
        with pytest.raises(versioning.UnsupportedMinor):
            versioning.declared_minor({"schema_minor": bad})
    assert (
        versioning.declared_minor({}) == 0 and versioning.declared_minor({"schema_minor": 1}) == 1
    )
    assert versioning.minor_problems({"schema_minor": 5})


# ------------------------------------------------------------------------------------------- chain
def test_chain_writer_keeps_an_explicit_event_id_and_numbers_the_rest():
    w = chain.ChainWriter(tenant_id="t")
    a = w.append(
        ts="2026-11-01T09:00:00Z",
        type="intent",
        actor="agent",
        case_id="",
        summary="a",
        event_id="custom-1",
    )
    b = w.append(ts="2026-11-01T09:00:01Z", type="intent", actor="agent", case_id="", summary="b")
    assert a["event_id"] == "custom-1" and b["event_id"] == "evt-0002"
    assert (
        chain.verify(w.events, chain.CHAIN_2)["ok"]
        and not chain.verify(w.events, chain.CHAIN)["ok"]
    )
    w1 = chain.ChainWriter()
    ev = w1.append(ts="2026-11-01T09:00:00Z", type="intent", actor="agent", case_id="", summary="a")
    assert "tenant_id" not in ev and w1.chain == chain.CHAIN
    with pytest.raises(ValueError, match="unknown chain format"):
        chain.event_hash(ev, "hash-chain/9")
    bad = dict(ev, tenant_id=5)
    with pytest.raises(TypeError):
        chain.event_hash(bad, chain.CHAIN_2)


def test_reseal_repairs_a_chain_of_either_format():
    for tenant in (None, "t"):
        w = chain.ChainWriter(tenant_id=tenant)
        for i in range(3):
            w.append(
                ts="2026-11-01T09:00:00Z", type="intent", actor="agent", case_id="", summary=str(i)
            )
        doc = {"audit": w.audit_block()}
        doc["audit"]["events"][1]["summary"] = "edited"
        assert not chain.verify(doc["audit"]["events"], doc["audit"]["chain"])["ok"]
        chain.reseal(doc)
        assert chain.verify(doc["audit"]["events"], doc["audit"]["chain"])["ok"]


# ------------------------------------------------------------------------------------------- honest
def with_(slug, fn):
    d = example(slug)
    fn(d)
    return d


def test_scripted_is_recognised_by_each_of_its_three_signs_alone():
    base = example("refunds")
    base["run"].update(mode="live", model="m")
    base["scorecard"]["scripted"] = False
    assert honest.is_scripted(base) is False
    for fn in (
        lambda d: d["run"].update(mode="scripted"),
        lambda d: d["run"].update(model="scripted"),
        lambda d: d["scorecard"].update(scripted=True),
    ):
        d = copy.deepcopy(base)
        fn(d)
        assert honest.is_scripted(d) is True


def test_capped_verdict_needs_both_scripted_and_green():
    d = example("askdata")
    d["scorecard"]["verdict"] = "amber"
    assert "capped_verdict" not in honest.status_ids(d) and honest.shown_verdict(d) == "amber"
    d["scorecard"]["verdict"] = "green"
    assert "capped_verdict" in honest.status_ids(d) and honest.shown_verdict(d) == "amber"
    live = example("askdata")
    live["run"].update(mode="live", model="m")
    live["scorecard"].update(scripted=False, verdict="green")
    assert honest.shown_verdict(live) == "green" and "capped_verdict" not in honest.status_ids(live)


def test_each_ledger_counter_alone_is_a_breach():
    assert not honest.ledger_breached(example("refunds")) and not honest.ledger_breached({})
    for key in ("non_terminal", "missing", "duplicated_effects", "reconciliation_gap"):
        d = example("refunds")
        d["ledger"][key] = 1
        assert honest.ledger_breached(d) and "ledger_breach" in honest.status_ids(d), key


def test_terms_and_canary_states_are_reported_one_by_one():
    d = example("refunds")
    assert {"terms_unrecorded", "canary_not_run", "load_assumed"} <= set(honest.status_ids(d))
    d["provider_terms"].update(no_training_setting="excluded", checked_on="2026-11-01")
    assert "terms_unrecorded" not in honest.status_ids(d)
    d["segregation"]["canary"] = {"ran": True, "foreign_hits": 0}
    ids = honest.status_ids(d)
    assert "canary_not_run" not in ids and "canary_breach" not in ids
    d["segregation"]["canary"] = {"ran": True, "foreign_hits": 2}
    assert "canary_breach" in honest.status_ids(d)
    d["load_profile"]["label"] = "measured"
    assert "load_measured" in honest.status_ids(d) and "load_assumed" not in honest.status_ids(d)
    bare = example("refunds")
    for k in ("provider_terms", "segregation", "load_profile", "ledger"):
        del bare[k]
    assert not {"terms_unrecorded", "canary_not_run", "load_assumed", "ledger_breach"} & set(
        honest.status_ids(bare)
    )


def test_fixture_is_the_default_and_run_is_explicit():
    d = example("refunds")
    assert honest.is_fixture(d)
    del d["honesty"]["kind"]
    assert honest.is_fixture(d)
    d["honesty"]["kind"] = "run"
    assert not honest.is_fixture(d)


# ------------------------------------------------------------------------------------------- kitcheck
def write(root, doc, *, page=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "export.json").write_text(json.dumps(doc), encoding="utf-8")
    if page:
        (root / "index.html").write_text(build_console.build(doc), encoding="utf-8")
        _, css, js = build_console.assets()
        (root / "_headers").write_text(
            build_console.headers_file(build_console.csp_for(js, css)), encoding="utf-8"
        )
    return root


@pytest.mark.parametrize(
    "strip",
    ["csp", "second_script", "no_style", "no_data"],
)
def test_page_that_lacks_any_one_console_part_is_not_a_console_page(tmp_path, strip):
    root = write(tmp_path / strip, example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    if strip == "csp":
        page = page.replace("Content-Security-Policy", "X-Other")
    elif strip == "second_script":
        page = page.replace("</body>", "<script>1</script></body>")
    elif strip == "no_style":
        page = page.replace("<style>", "<b>").replace("</style>", "</b>")
    else:
        page = page.replace('id="export-data"', 'id="other-data"')
    (root / "index.html").write_text(page, encoding="utf-8")
    assert any("not a console page" in p.message for p in kitcheck.run(root)["problems"]), strip


def test_embedded_data_that_is_not_strict_json_is_reported(tmp_path):
    root = write(tmp_path / "nj", example("refunds"))
    page = (root / "index.html").read_text(encoding="utf-8")
    start = page.index('id="export-data">') + len('id="export-data">')
    end = page.index("</script>", start)
    (root / "index.html").write_text(
        page[:start] + '{"a": 1, "a": 2}' + page[end:], encoding="utf-8"
    )
    assert any("not strict JSON" in p.message for p in kitcheck.run(root)["problems"])


def test_symlink_and_subdirectory_messages_are_specific(tmp_path, monkeypatch):
    root = write(tmp_path / "sl", example("refunds"), page=False)
    (root / "sub").mkdir()
    msgs = {p.message for p in kitcheck.run(root)["problems"]}
    assert "sub-directories are not allowed" in msgs
    real = type(root).is_symlink
    monkeypatch.setattr(
        type(root), "is_symlink", lambda self: self.name == "export.json" or real(self)
    )
    res = kitcheck.run(root)
    assert "symbolic links are not allowed" in {p.message for p in res["problems"]}
    assert any(p.code == "MISSING_FILE" and p.path == "export.json" for p in res["problems"])


def test_file_size_limit_is_inclusive(tmp_path, monkeypatch):
    root = write(tmp_path / "sz", example("refunds"), page=False)
    size = (root / "export.json").stat().st_size
    monkeypatch.setattr(kitcheck, "MAX_FILE_BYTES", size)
    assert "UNEXPECTED_FILE" not in {p.code for p in kitcheck.run(root)["problems"]}
    monkeypatch.setattr(kitcheck, "MAX_FILE_BYTES", size - 1)
    assert "UNEXPECTED_FILE" in {p.code for p in kitcheck.run(root)["problems"]}


def test_deny_scan_does_not_choke_on_a_directory_with_a_file_suffix(tmp_path):
    root = write(tmp_path / "dn", example("refunds"), page=False)
    (root / "folder.json").mkdir()
    res = kitcheck.run(root, deny=["nothing-matches-this"])
    assert "UNEXPECTED_FILE" in {p.code for p in res["problems"]}  # reported, and no exception


def test_typed_codes_carry_through_the_cli_json_shape():
    p = Typed("CHAIN", "x")
    assert validate.Problem(p.code, "a", str(p)).as_dict() == {
        "code": "CHAIN",
        "path": "a",
        "message": "x",
    }


def test_every_example_is_a_conforming_directory_and_hash_stable(tmp_path):
    for slug in SLUGS:
        root = write(tmp_path / slug, example(slug))
        res = kitcheck.run(root, demo=slug)
        assert res["ok"], (slug, [str(p) for p in res["problems"]])
        digest = hashlib.sha256((root / "export.json").read_bytes()).hexdigest()
        assert len(digest) == 64


def _deep(levels, leaf):
    node = {"type": "object", "properties": leaf}
    for _ in range(levels):
        node = {"type": "object", "properties": {"p": node}}
    return node


def test_additive_diff_survives_odd_schema_shapes_and_has_an_exact_depth_limit():
    # boolean sub-schemas and a keyword present on only one side must not crash the comparison
    assert versioning.additive_diff({"properties": {"x": True}}, {"properties": {"x": True}}) == []
    only_old = {"properties": {"a": {"type": "array", "items": {"type": "string"}}}}
    only_new = {"properties": {"a": {"type": "array"}}}
    assert versioning.additive_diff(only_old, only_new) == []
    assert versioning.additive_diff(only_new, only_old) == []
    ap_old = {"properties": {"a": {"type": "object", "additionalProperties": {"type": "integer"}}}}
    ap_new = {"properties": {"a": {"type": "object", "additionalProperties": True}}}
    assert versioning.additive_diff(ap_old, ap_new) == []
    assert versioning.additive_diff(ap_new, ap_old) == []
    bare = {"properties": {"a": {"type": "object"}}}
    with_ap = {"properties": {"a": {"type": "object", "additionalProperties": {"type": "integer"}}}}
    assert "a schema was added" in " ".join(
        versioning.additive_diff(bare, with_ap)
    )  # a tightening, now flagged
    assert versioning.additive_diff(with_ap, bare) == []
    # the walk is bounded: a removed property at nesting depth 30 is still reported, one at depth 31 is out of reach
    old_30, new_30 = _deep(30, {"x": {"type": "string"}}), _deep(30, {})
    assert "property removed" in " ".join(versioning.additive_diff(old_30, new_30))
    assert versioning.additive_diff(_deep(31, {"x": {"type": "string"}}), _deep(31, {})) == []


def test_an_honestly_failing_chain_is_flagged_even_when_the_claim_agrees():
    d = example("refunds")
    d["audit"]["events"][3]["summary"] += " edited"
    d["audit"]["verify"] = chain.verify(
        d["audit"]["events"], d["audit"]["chain"]
    )  # the export admits the failure
    assert d["audit"]["verify"]["ok"] is False
    assert "chain_bad" in honest.status_ids(d)
    assert "chain_bad" not in honest.status_ids(example("refunds"))
