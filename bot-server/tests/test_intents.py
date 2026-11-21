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
    (MEM, "5gb", 5120), (MEM, "1.5gb", 1536), (MEM, "512m", 512), (MEM, "512Mi", 512), (MEM, "0.5 GB", 512), (MEM, 3072, 3072),
    (MEM, "+512mb", 2560), (MEM, "-1gb", 1024), (MEM, "75%", 1536), (MEM, "200%", 4096),
    (CPU, "80%", 1200), (CPU, "50%", 750), (CPU, "2.5 cores", 2500), (CPU, "750m", 750), (CPU, "2", 2000),
    (CPU, "1500", 1500), (CPU, "+25%", 1875), (CPU, "-250m", 1250),
    (TIME, "90s", 90), (TIME, "2 minutes", 120), (TIME, "+30s", 90),
    ("/replicas", "-1", 1), ("/replicas", 5, 5), ("/replicas", "5", 5), ("/replicas", "+1", 3),
])
def test_resolve_number(path, raw, expected):
    from jsonpointer import resolve_pointer

    assert resolve_number(path, raw, resolve_pointer(VALUES, path)) == expected


def test_rounding_ties_go_away_from_the_current_value():
    assert resolve_number(CPU, "50%", 2501) == 1250  # a decrease: 1250.5 -> 1250
    assert resolve_number(CPU, "+0.5m", 1000) == 1001  # an increase: 1000.5 -> 1001
    assert resolve_number("/replicas", "-25%", 2) == 1  # never rounds back into a no-op


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
    patch, unchanged = compile_intents([
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
    assert unchanged == []


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


def test_duplicates_dropped_and_no_ops_reported_as_unchanged():
    patch, unchanged = compile_intents([
        {"op": "replace", "path": "/image", "value": "web:1.0"},  # already the current value
        {"op": "replace", "path": MEM, "value": "4gb"},
        {"op": "replace", "path": MEM, "value": "4gb"},  # repeat
        {"op": "replace", "path": "/replicas", "value": 2},  # already 2
    ], VALUES)
    assert patch == [{"op": "replace", "path": MEM, "value": 4096}]
    assert unchanged == ["/image", "/replicas"]


def test_conflicting_values_for_one_path_are_rejected():
    with pytest.raises(IntentError, match="conflicting values"):
        compile_intents([{"op": "replace", "path": "/replicas", "value": 3},
                         {"op": "replace", "path": "/replicas", "value": 5}], VALUES)


def test_empty_operations_are_allowed():
    assert compile_intents([], VALUES) == ([], [])


def test_new_keys_must_be_named_in_the_request():
    add = [{"op": "add", "path": "/envs", "key": "CPU_LIMIT_PERCENTAGE", "value": "25%"}]
    with pytest.raises(IntentError, match="only add keys the request names"):
        compile_intents(add, VALUES, request="increase cpu limit env by 25%")
    ok = [{"op": "add", "path": "/envs", "key": "LOG_LEVEL", "value": "debug"}]
    assert compile_intents(ok, VALUES, request="add env log_level=debug")[0][0]["path"] == "/envs/LOG_LEVEL"


R = lambda path, value: {"op": "replace", "path": path, "value": value}  # noqa: E731


@pytest.mark.parametrize("intents, request_text, message", [
    ([R(CPU, "6500")], "set web cpu limit to 65", "doesn't follow"),  # invented value
    ([R(CPU, "500m")], "set web cpu limit to 500 cores", "doesn't follow"),  # unit swapped
    ([R(MEM, "1100m")], "increase web memory by 10%", "doesn't follow"),  # model got the math wrong
    ([R(MEM, "750m")], "lower web memory to 75%", "doesn't follow"),  # 75% of 2048 is 1536
    ([R("/image", "evil:1")], "make web faster", "isn't in the request"),  # vague request
    ([R("/gate/enabled", True)], "set web image to x", "isn't in the request"),  # flag with no flag words
    ([{"op": "add", "path": "/envs", "key": "MEMORY", "value": "3gb"}], "set web memory to 3gb", "asks for one"),
    ([{"op": "add", "path": "/envs", "key": "BAR", "value": "bar"}], "set the tenant env to bar", "is the value"),
    ([{"op": "add", "path": "/envs", "key": "ENV", "value": "x"}], "add an environment variable x", "names"),
    ([{"op": "add", "path": "/envs", "key": "foo", "value": ""}], "set the tenant env to foo", "needs a value"),
    ([R("/res/cpu/requestMilliCPU", "500m")], "halve web cpu limit", "doesn't mention resource requests"),
    ([R("/replicas", "2.5")], "set web replicas to 2.5", "whole number"),
    ([R(CPU, "65")], "set web cpu limit to 65", "ambiguous"),
])
def test_grounding_rejects_what_the_user_did_not_say(intents, request_text, message):
    with pytest.raises(IntentError, match=message):
        compile_intents(intents, VALUES, request=request_text)


@pytest.mark.parametrize("intents, request_text, value", [
    ([R(MEM, "3072MiB")], "set web memory to 3gb", 3072),  # same amount, different spelling
    ([R(MEM, "1.5gb")], "give web one and a half gigs of memory", 1536),
    ([R(CPU, "200%")], "double the web cpu limit", 3000),
    ([R("/replicas", "-1")], "reduce web replicas by 1", 1),
    ([R("/replicas", "-1")], "set web replicas to -1", -1),  # absolute, not relative: the schema refuses it
    ([R(MEM, ".5gb")], "set web memory to .5gb", 512),
    ([R(CPU, "1875m")], "increase web cpu limit by 25%", 1875),  # the model did the math, and got it right
    ([R(CPU, "750")], "halve the web cpu limit", 750),
    ([R("/gate/enabled", True)], "enable the web readiness gate", True),
])
def test_grounding_accepts_what_the_user_did_say(intents, request_text, value):
    patch, _ = compile_intents(intents, VALUES, request=request_text)
    assert patch[0]["value"] == value


def test_limit_below_request_is_rejected():
    from intents import check_limits

    with pytest.raises(IntentError, match="below requestMilliCPU"):
        check_limits({"cpu": {"limitMilliCPU": 50, "requestMilliCPU": 1000}})
    check_limits({"cpu": {"limitMilliCPU": 1000, "requestMilliCPU": 1000}})


def test_unrequested_request_ops_are_dropped_and_requests_clamped():
    values = {"res": {"cpu": {"limitMilliCPU": 2500, "requestMilliCPU": 2000}}}
    limit, request = "/res/cpu/limitMilliCPU", "/res/cpu/requestMilliCPU"
    patch, _ = compile_intents([R(limit, "50%"), R(request, "50%")], values, request="halve the web cpu limit")
    # the model's request edit is dropped; the request is clamped because the new limit crosses it
    assert patch == [{"op": "replace", "path": limit, "value": 1250}, {"op": "replace", "path": request, "value": 1250}]


def test_request_is_not_clamped_when_the_limit_stays_above_it():
    values = {"res": {"cpu": {"limitMilliCPU": 1500, "requestMilliCPU": 1000}}}
    patch, _ = compile_intents([R("/res/cpu/limitMilliCPU", "80%")], values, request="lower web cpu limit to 80%")
    assert patch == [{"op": "replace", "path": "/res/cpu/limitMilliCPU", "value": 1200}]


def test_padding_no_ops_are_dropped_quietly():
    patch, unchanged = compile_intents(
        [R("/ports/0/port", 7777), R(CPU, "1500")],  # the model padded with the cpu limit's current value
        VALUES, request="set web port to 7777")
    assert patch == [{"op": "replace", "path": "/ports/0/port", "value": 7777}]
    assert unchanged == []  # not reported as "already set": the user never asked about cpu


def test_reply_that_is_only_padding_is_rejected():
    with pytest.raises(IntentError, match="none of these operations match the request"):
        compile_intents([R(CPU, "1500"), R("/image", "web:1.0")], VALUES, request="set web hostname to x.example.com")


@pytest.mark.parametrize("value", ["3gb", "1.5 cores", "512Mi"])
def test_quantity_aimed_at_a_text_field_is_rejected(value):
    with pytest.raises(IntentError, match="looks like a quantity"):
        compile_intents([R("/image", value)], VALUES, request=f"set web memory to {value}")


def test_percentages_and_versions_are_fine_in_text_fields():
    patch, _ = compile_intents([R("/image", "web:2.0")], VALUES, request="deploy web:2.0")
    assert patch[0]["value"] == "web:2.0"
