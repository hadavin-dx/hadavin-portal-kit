"""additive_diff must see every way a schema change can reject something the old schema accepted (review M4)."""

import copy

import pytest
from hadavin_kit import versioning


def base():
    return {
        "type": "object",
        "properties": {
            "s": {"type": "string", "maxLength": 100, "minLength": 1, "pattern": "^[a-z]+$"},
            "n": {"type": "integer", "minimum": 0, "maximum": 10},
            "a": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "o": {"type": "object", "additionalProperties": {"type": "integer"}},
            "u": {"type": "object"},
        },
    }


def changed(fn):
    new = base()
    fn(new)
    return versioning.additive_diff(base(), new)


P = "properties"
CASES = {
    "maxLength lowered": (lambda n: n[P]["s"].update(maxLength=50), "/s: maxLength"),
    "minLength raised": (lambda n: n[P]["s"].update(minLength=2), "/s: minLength"),
    "pattern changed": (lambda n: n[P]["s"].update(pattern="^[a-z0-9]+$"), "/s: pattern"),
    "pattern added": (lambda n: n[P]["n"].update(pattern="^1$"), "/n: pattern"),
    "minimum raised": (lambda n: n[P]["n"].update(minimum=1), "/n: minimum"),
    "maximum lowered": (lambda n: n[P]["n"].update(maximum=9), "/n: maximum"),
    "maxItems lowered": (lambda n: n[P]["a"].update(maxItems=4), "/a: maxItems"),
    "maxItems added": (lambda n: n[P]["u"].update(maxProperties=3), "/u: maxProperties"),
    "additionalProperties true to false": (
        lambda n: n.update(additionalProperties=False),
        ": additionalProperties",
    ),
    "additionalProperties schema added": (
        lambda n: n[P]["u"].update(additionalProperties={"type": "string"}),
        "/u: additionalProperties",
    ),
    "oneOf added": (lambda n: n[P]["s"].update(oneOf=[{"const": "a"}]), "/s: oneOf"),
    "anyOf added": (lambda n: n[P]["s"].update(anyOf=[{"const": "a"}]), "/s: anyOf"),
    "allOf added": (lambda n: n[P]["s"].update(allOf=[{"minLength": 3}]), "/s: allOf"),
    "not added": (lambda n: n[P]["s"].update(**{"not": {"const": "a"}}), "/s: not"),
    "if/then added": (
        lambda n: n[P]["n"].update(**{"if": {"const": 1}, "then": {"const": 2}}),
        "/n: if",
    ),
    "patternProperties added": (
        lambda n: n[P]["u"].update(patternProperties={"^x": {"type": "string"}}),
        "/u: patternProperties",
    ),
    "propertyNames added": (
        lambda n: n[P]["u"].update(propertyNames={"pattern": "^[a-z]+$"}),
        "/u: propertyNames",
    ),
    "prefixItems added": (
        lambda n: n[P]["a"].update(prefixItems=[{"const": "x"}]),
        "/a: prefixItems",
    ),
    "uniqueItems added": (lambda n: n[P]["a"].update(uniqueItems=True), "/a: uniqueItems"),
    "contains added": (lambda n: n[P]["a"].update(contains={"const": "x"}), "/a: contains"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_each_tightening_is_flagged(name):
    fn, text = CASES[name]
    got = changed(fn)
    assert any(text in g for g in got), (name, got)


def test_loosenings_and_additions_are_not_flagged():
    def loosen(n):
        n[P]["s"].update(maxLength=200, minLength=0)
        n[P]["n"].update(minimum=-1, maximum=11)
        n[P]["a"].update(maxItems=6)
        n[P]["new"] = {
            "type": "string",
            "pattern": "^x$",
            "maxLength": 3,
        }  # a brand-new optional key
        n["additionalProperties"] = True

    assert changed(loosen) == []
    assert versioning.additive_diff(base(), base()) == []


def test_changed_but_unchanged_combinator_is_fine_and_a_changed_one_is_not():
    old = base()
    old[P]["s"]["oneOf"] = [{"const": "a"}, {"const": "b"}]
    same = copy.deepcopy(old)
    assert versioning.additive_diff(old, same) == []
    other = copy.deepcopy(old)
    other[P]["s"]["oneOf"].append({"const": "c"})
    assert any("oneOf" in g for g in versioning.additive_diff(old, other))


def test_accepted_entries_are_honoured_and_a_stale_one_is_reported():
    new = base()
    new[P]["s"]["maxLength"] = 50
    assert versioning.additive_diff(base(), new, {"/s: maxLength": "reviewed"}) == []
    stale = versioning.additive_diff(base(), base(), {"/s: maxLength": "reviewed"})
    assert any("stale entry" in s for s in stale)


def test_the_real_schema_change_needs_exactly_the_listed_exceptions():
    import json

    from hadavin_kit import validate
    from hadavin_kit.testing import KIT

    old = json.loads(
        (KIT / "demo_only" / "fixtures" / "compat-v1.0" / "demo-export.v1.0.schema.json").read_text(
            encoding="utf-8"
        )
    )
    new = validate.schema()
    raw = versioning.additive_diff(old, new)
    assert len(raw) == len(versioning.ACCEPTED_TIGHTENINGS) and raw
    assert versioning.additive_diff(old, new, versioning.ACCEPTED_TIGHTENINGS) == []
    assert all(reason for reason in versioning.ACCEPTED_TIGHTENINGS.values())
