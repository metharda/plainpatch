import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from intents import (
    IntentError,
    compile_intents,
    field_kind,
    intent_format,
    leaf_paths,
    resolve_number,
)

VALUES = {
    "replicas": 2,
    "res": {"memory": {"limitMiB": 2048}, "cpu": {"limitMilliCPU": 1500}},
    "proxy": {"readSeconds": 60},
    "image": "web:1.0",
    "gate": {"enabled": False},
    "envs": {"TENANT": "x"},
    "labels": {"app.io/name": "web"},
    "empty": {},
    "ports": [{"port": 80}],
}
MEM, CPU, TIME = "/res/memory/limitMiB", "/res/cpu/limitMilliCPU", "/proxy/readSeconds"


def test_leaf_paths_group_by_writable_type():
    groups = leaf_paths(VALUES)
    assert groups["number"] == ["/replicas", MEM, CPU, TIME, "/ports/0/port"]
    assert groups["text"] == ["/image", "/envs/TENANT", "/labels/app.io~1name"]  # "/" in keys escaped
    assert groups["flag"] == ["/gate/enabled"]
    assert groups["map"] == ["/envs"]  # "/empty" is not a map container, so nothing can be added there


def test_grammar_pins_value_types_and_only_lists_existing_paths():
    shapes = intent_format(VALUES)["properties"]["operations"]["items"]["anyOf"]
    by_type = {}
    for shape in shapes:
        props = shape["properties"]
        by_type.setdefault(props["op"]["enum"][0], []).append(props)
    text, flag, number = by_type["replace"]
    assert text["value"] == {"type": "string"} and "/image" in text["path"]["enum"]
    assert flag["value"] == {"type": "boolean"} and flag["path"]["enum"] == ["/gate/enabled"]
    assert MEM in number["path"]["enum"] and "/image" not in number["path"]["enum"]
    assert by_type["add"][0]["path"]["enum"] == ["/envs"]
    assert intent_format(VALUES)["properties"]["operations"]["maxItems"] == 8


@pytest.mark.parametrize("path, kind", [(MEM, "memory"), (CPU, "cpu"), (TIME, "time"), ("/replicas", "plain")])
def test_field_kind_from_suffix(path, kind):
    assert field_kind(path) == kind


@pytest.mark.parametrize("path, raw, expected", [
    (MEM, "5gb", 5120), (MEM, "1.5gb", 1536), (MEM, "512Mi", 512), (MEM, "0.5 GB", 512), (MEM, 3072, 3072),
    (MEM, "+512mb", 2560), (MEM, "-1gb", 1024), (MEM, "75%", 1536), (MEM, "200%", 4096),
    (CPU, "80%", 1200), (CPU, "50%", 750), (CPU, "2.5 cores", 2500), (CPU, "750m", 750), (CPU, "2", 2000),
    (CPU, "1500", 1500), (CPU, "+25%", 1875), (CPU, "-250m", 1250),
    (TIME, "90s", 90), (TIME, "2 minutes", 120), (TIME, "+30s", 90),
    ("/replicas", "-1", 1), ("/replicas", 5, 5), ("/replicas", "5", 5), ("/replicas", "+1", 3),
])
def test_resolve_number(path, raw, expected):
    from jsonpointer import resolve_pointer

    assert resolve_number(path, raw, resolve_pointer(VALUES, path)) == expected


def test_resolve_number_rounds_half_up_on_integer_fields():
    assert resolve_number(CPU, "50%", 2501) == 1251  # 1250.5 -> 1251, not banker's 1250


@pytest.mark.parametrize("path, raw, message", [
    (MEM, "lots", "can't read"),
    (MEM, "2 cores", "doesn't fit a memory field"),
    ("/replicas", "2gb", "doesn't fit a plain field"),
    (MEM, True, "needs a number"),
    (MEM, float("inf"), "finite"),
])
def test_resolve_number_rejects_with_actionable_feedback(path, raw, message):
    with pytest.raises(IntentError, match=message):
        resolve_number(path, raw, 2048)


def test_relative_change_needs_a_current_number():
    with pytest.raises(IntentError, match="no current number"):
        resolve_number("/missing", "+1", None)


def test_compile_turns_intents_into_rfc6902():
    patch = compile_intents([
        {"op": "replace", "path": MEM, "value": "4gb"},
        {"op": "replace", "path": "/image", "value": "web:2.0"},
        {"op": "replace", "path": "/gate/enabled", "value": True},
        {"op": "add", "path": "/envs", "key": " LOG/LEVEL ", "value": "debug"},
        {"op": "remove", "path": "/envs/TENANT"},
    ], VALUES)
    assert patch == [
        {"op": "replace", "path": MEM, "value": 4096},
        {"op": "replace", "path": "/image", "value": "web:2.0"},
        {"op": "replace", "path": "/gate/enabled", "value": True},
        {"op": "add", "path": "/envs/LOG~1LEVEL", "value": "debug"},
        {"op": "remove", "path": "/envs/TENANT"},
    ]


@pytest.mark.parametrize("intents, message", [
    (None, "operations"),
    (["nope"], "string 'path'"),
    ([{"op": "replace", "path": MEM}], "needs a 'value'"),
    ([{"op": "add", "path": "/envs", "value": "x"}], "non-empty 'key'"),
    ([{"op": "add", "path": "/envs", "key": "  ", "value": "x"}], "non-empty 'key'"),
    ([{"op": "copy", "path": MEM, "from": CPU}], "unknown op"),
])
def test_compile_rejects_malformed_intents(intents, message):
    with pytest.raises(IntentError, match=message):
        compile_intents(intents, VALUES)
