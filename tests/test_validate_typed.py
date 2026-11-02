"""Strict validation with typed errors: unknown keys, wrong types, missing keys, confusable ids, oversize fields."""

import copy
import json

import pytest
from hadavin_kit import chain, export_rules, problems, rules_v11, strict_json, validate
from hadavin_kit.testing import codes, example, messages

KITCHECK_ONLY = {
    "UNEXPECTED_FILE", "MISSING_FILE", "PAGE_MISMATCH", "REPORT_MISMATCH", "DENYLIST_HIT", "NOT_JSON", "KIT_LOCK",
}  # fmt: skip


def unknown_root(d):
    d["surprise"] = 1


def unknown_nested(d):
    d["cases"][0]["steps"][0]["surprise"] = 1


def unknown_in_ledger(d):
    d["ledger"]["extra"] = 0


def seq_is_string(d):
    d["audit"]["events"][0]["seq"] = "1"


def bool_for_int(d):
    d["run"]["seed"] = True


def list_for_object(d):
    d["scorecard"]["thresholds"] = []


def float_seq(d):
    d["audit"]["events"][0]["seq"] = 1.0


def missing_case_key(d):
    del d["cases"][0]["outcome"]


def missing_top(d):
    del d["registry"]


def missing_ledger_key(d):
    del d["ledger"]["missing"]


def bad_enum(d):
    d["cases"][0]["outcome"] = "maybe"


def negative_count(d):
    d["ledger"]["missing"] = -1


def cyrillic_case_id(d):
    d["cases"][0]["case_id"] = "r\u0435funds-e-001"  # Cyrillic e


def fullwidth_tenant(d):
    d["tenant_id"] = "\uff54enant"  # fullwidth t


def nonascii_receipt(d):
    d["cases"][0]["receipt_id"] = "rcpt-\u0430"


def nonascii_hz_key(d):
    d["scorecard"]["hard_zeros"]["m\u043eney_auto_approved"] = 0  # Cyrillic o


def long_text(d):
    d["cases"][0]["input_summary"] = "x" * 2001


def long_arg_string(d):
    d["approvals"][0]["args"]["note"] = "y" * (export_rules.MAX_STRING + 1)


def too_many_cases(d):
    d["cases"] = d["cases"] * 2001


def bidi_text(d):
    d["cases"][0]["input_summary"] = "pay \u202eevil"


def zero_width_id_in_text(d):
    d["registry"]["owner_role"] = "ap\u200bmanager"


def too_deep(d):
    cur = d["approvals"][0]["args"]
    for _ in range(export_rules.MAX_DEPTH + 5):
        cur["n"] = {}
        cur = cur["n"]


CASES = {
    "UNKNOWN_KEY": [unknown_root, unknown_nested, unknown_in_ledger],
    "WRONG_TYPE": [seq_is_string, bool_for_int, list_for_object],
    "MISSING_KEY": [missing_case_key, missing_top, missing_ledger_key],
    "BAD_VALUE": [bad_enum, negative_count],
    "CONFUSABLE_ID": [cyrillic_case_id, fullwidth_tenant, nonascii_receipt, nonascii_hz_key],
    "OVERSIZE": [long_text, long_arg_string, too_many_cases],
    "BAD_CHARACTER": [bidi_text, zero_width_id_in_text],
}


@pytest.mark.parametrize(
    ("code", "fn"),
    [(c, f) for c, fs in CASES.items() for f in fs],
    ids=lambda x: getattr(x, "__name__", x),
)
def test_mutation_gets_its_typed_code(code, fn):
    d = example("refunds")
    fn(d)
    assert code in codes(d), (fn.__name__, [str(p) for p in validate.validate(d)[:3]])


def test_unmutated_baseline_has_no_problem_of_any_code():
    for slug in ("refunds", "apexc", "intake", "dealdesk", "askdata"):
        assert validate.validate(example(slug)) == []


def test_float_that_looks_like_an_integer_is_rejected():
    d = example("refunds")
    float_seq(d)
    assert any("must be an integer" in m for m in messages(d))


def test_too_deep_nesting_is_rejected_without_crashing():
    d = example("refunds")
    too_deep(d)
    assert "RULE" in codes(d) or "OVERSIZE" in codes(d)


def test_document_size_limit(monkeypatch):
    monkeypatch.setattr(export_rules, "MAX_DOC_BYTES", 1000)
    assert "OVERSIZE" in codes(example("refunds"))


def test_non_object_documents_are_rejected():
    for bad in ([], "x", 1, None, [{"schema": "hadavin.demo-export/1"}]):
        assert {p.code for p in validate.validate(bad)} == {"WRONG_TYPE"}


def test_load_failures_are_typed_not_json(tmp_path):
    cases = {
        "dup": '{"a": 1, "a": 2}',
        "nan": '{"a": NaN}',
        "huge": '{"a": 1e999}',
        "array": "[1]",
        "garbage": "{",
    }
    for name, text in cases.items():
        p = tmp_path / f"{name}.json"
        p.write_text(text, encoding="utf-8")
        doc, found = validate.load_and_validate(p)
        assert doc is None and [x.code for x in found] == ["NOT_JSON"], name
    doc, found = validate.load_and_validate(tmp_path / "missing.json")
    assert doc is None and found[0].code == "NOT_JSON"


def test_every_code_is_produced_somewhere_and_nothing_else_exists():
    produced = set(CASES) | KITCHECK_ONLY | {"UNSUPPORTED_MINOR", "MINOR_TOO_LOW", "CHAIN", "RULE"}
    assert produced == set(problems.CODES)
    with pytest.raises(ValueError):
        problems.Typed("MADE_UP", "x")
    with pytest.raises(ValueError):
        validate.Problem("MADE_UP", "", "x")


def test_chain_and_rule_codes():
    d = example("refunds")
    d["audit"]["events"][1]["summary"] += "!"
    assert "CHAIN" in codes(d)
    d = example("refunds")
    d["approvals"][0]["approval_id"] = d["approvals"][1]["approval_id"]
    assert "RULE" in codes(d)


def test_problem_messages_never_echo_unbounded_input():
    d = example("refunds")
    d["cases"][0]["outcome"] = "z" * 100_000
    for p in validate.validate(d):
        assert len(str(p)) < 700


def test_hostile_ids_cannot_collide_after_normalisation():
    """NFKC-equal or case-equal hard-zero keys are the same key (look-alike identifiers)."""
    assert rules_v11.keys_unique(["HZ-01", "hz-01"], "x")
    assert rules_v11.keys_unique(["\uff28\uff3a-01", "HZ-01"], "x")
    assert rules_v11.keys_unique(["HZ-01", "HZ-02"], "x") == []


def test_typed_problem_is_still_a_string_and_survives_copy():
    p = problems.Typed("RULE", "hello")
    assert p == "hello" and "ell" in p and copy.copy(p) == "hello"
    assert problems.code_of(p) == "RULE" and problems.code_of("plain") == "RULE"


def test_json_roundtrip_of_example_is_stable():
    d = example("refunds")
    again = strict_json.loads(json.dumps(d))
    assert again == d and chain.verify(again["audit"]["events"], again["audit"]["chain"])["ok"]
