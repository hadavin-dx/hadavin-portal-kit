"""The JSON schema is exercised at every location the example exports reach (this is what lets the mutation harness
mutate the schema file itself): each required key must be required, each object must reject an unknown key, every
enum value must be accepted and a foreign value refused, every pattern must refuse a string that breaks it."""

import copy

import pytest
from hadavin_kit import strict_json, validate
from hadavin_kit.testing import KIT, SLUGS, compat, example
from jsonschema import Draft202012Validator

SCHEMA = validate.schema()


def res(node):
    while isinstance(node, dict) and "$ref" in node:
        node = SCHEMA["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return node


def descend(node, token):
    node = res(node)
    if not isinstance(node, dict):
        return None, None
    if isinstance(token, int):
        it = node.get("items")
        return (res(it), "[]") if isinstance(it, dict) else (None, None)
    props = node.get("properties", {})
    if token in props:
        return res(props[token]), token
    ap = node.get("additionalProperties")
    if isinstance(ap, dict):
        return res(ap), "*"
    return None, None


def walk(value, node, spath, out):
    if node is None:
        return
    out.setdefault(spath, (node, value))
    if isinstance(value, dict):
        for k, v in value.items():
            child, label = descend(node, k)
            walk(v, child, (*spath, label), out)
    elif isinstance(value, list):
        for v in value:
            child, label = descend(node, 0)
            walk(v, child, (*spath, label), out)


def locations():
    out = {}
    docs = [example(s) for s in SLUGS] + [compat(s) for s in SLUGS]
    docs.append(strict_json.load_object(KIT / "demo_only" / "fixtures" / "green-testdouble.json"))
    for d in docs:
        walk(d, SCHEMA, ("root",), out)
    return out


try:
    LOCS = locations()
except OSError:  # the sample-data folder is deleted (cut test): the walk tests below are skipped
    LOCS = {}


def errors(node, value):
    wrapper = {**node, "$defs": SCHEMA["$defs"]}
    return list(Draft202012Validator(wrapper).iter_errors(value))


def ids(items):
    return ["/".join(map(str, k)) for k, _ in items]


REQUIRED = [(k, v) for k, v in LOCS.items() if isinstance(v[1], dict) and v[0].get("required")]
OBJECTS = [
    (k, v)
    for k, v in LOCS.items()
    if isinstance(v[1], dict) and v[0].get("additionalProperties") is False
]
ENUMS = [(k, v) for k, v in LOCS.items() if "enum" in v[0] and not isinstance(v[1], (dict, list))]
PATTERNS = [(k, v) for k, v in LOCS.items() if "pattern" in v[0] and isinstance(v[1], str)]


def test_the_walk_reaches_a_meaningful_part_of_the_schema():
    if not LOCS:
        pytest.skip("needs the sample-data folder (cut test)")
    assert (
        len(LOCS) > 150
        and len(REQUIRED) > 40
        and len(OBJECTS) > 40
        and len(ENUMS) > 20
        and len(PATTERNS) > 15
    )


@pytest.mark.parametrize(("spath", "pair"), REQUIRED, ids=ids(REQUIRED))
def test_every_required_key_is_required(spath, pair):
    node, inst = pair
    assert not errors(node, inst)
    for key in node["required"]:
        if key not in inst:
            continue
        broken = {k: v for k, v in inst.items() if k != key}
        assert any(e.validator == "required" and key in e.message for e in errors(node, broken)), (
            spath,
            key,
        )


@pytest.mark.parametrize(("spath", "pair"), OBJECTS, ids=ids(OBJECTS))
def test_every_closed_object_rejects_an_unknown_key(spath, pair):
    node, inst = pair
    broken = {**inst, "zz_unknown_key": 1}
    assert any(e.validator == "additionalProperties" for e in errors(node, broken)), spath


@pytest.mark.parametrize(("spath", "pair"), ENUMS, ids=ids(ENUMS))
def test_every_enum_value_is_accepted_and_a_foreign_one_refused(spath, pair):
    node, _ = pair
    for v in node["enum"]:
        assert not [e for e in errors(node, v) if e.validator == "enum"], (spath, v)
    assert any(e.validator == "enum" for e in errors(node, "zz-not-a-member")), spath


@pytest.mark.parametrize(("spath", "pair"), PATTERNS, ids=ids(PATTERNS))
def test_every_pattern_refuses_a_string_that_breaks_it(spath, pair):
    node, inst = pair
    assert not [e for e in errors(node, inst) if e.validator == "pattern"]
    assert any(e.validator == "pattern" for e in errors(node, "!! not an id \n")), spath


def test_enum_value_checks_are_not_vacuous():
    node = {"enum": ["a", "b"]}
    assert errors(node, "c") and not errors(node, "a")
    assert copy.deepcopy(node) == node


def test_schema_equals_its_approved_facts():
    """The schema cannot be the only judge of itself: required lists, enums, patterns and closed objects are compared
    with an approval file, so loosening any of them is a visible diff (update deliberately with
    `python -m hadavin_kit.docgen --update-schema-facts`)."""
    import json

    from hadavin_kit import docgen

    approved = json.loads(docgen.FACTS.read_text(encoding="utf-8"))
    live = docgen.schema_facts(SCHEMA)
    assert live == approved
    assert len(approved) > 90


def test_the_facts_check_notices_each_kind_of_loosening():
    from hadavin_kit import docgen

    base = docgen.schema_facts(SCHEMA)
    for mutate in (
        lambda s: s["$defs"]["case"]["required"].remove("outcome"),
        lambda s: s["$defs"]["case"].update(additionalProperties=True),
        lambda s: s["$defs"]["case"]["properties"]["outcome"]["enum"].remove("blocked"),
        lambda s: s["$defs"]["slug"].pop("pattern"),
    ):
        broken = copy.deepcopy(SCHEMA)
        mutate(broken)
        assert docgen.schema_facts(broken) != base
