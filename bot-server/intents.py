"""Intent operations: what the model may say, and how code turns it into RFC 6902.

The model only *extracts* what to change: a path from a closed list, an amount, a unit
and a mode. Unit conversion and arithmetic happen here, in code, where they are exact
and testable. Small models are good at the first job and unreliable at the second
("5gb" -> 5242, "50% of 500" -> 750).
"""
import math
import re

import jsonpointer

MAP_CONTAINERS = ("envs",)  # objects where new keys may be added

# field kind (from the path's suffix) -> unit spellings -> factor into the field's unit
UNITS = {
    "memory": {"": 1, "m": 1, "mb": 1, "mi": 1, "mib": 1, "g": 1024, "gb": 1024, "gi": 1024, "gib": 1024},
    "cpu": {"m": 1, "millicore": 1, "millicores": 1, "millicpu": 1,
            "core": 1000, "cores": 1000, "cpu": 1000, "cpus": 1000},  # bare numbers: see to_field_unit
    "time": {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
             "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60},
    "plain": {"": 1},
}
# "+512mb", "-1", "2.5 cores", "80%"
QUANTITY = re.compile(r"^\s*([+-]?)\s*(\d+(?:\.\d+)?)\s*([a-z%]*)\s*$", re.IGNORECASE)
EXAMPLES = {"memory": "'2gb', '512mb', '+1gb', '80%'", "cpu": "'1.5 cores', '750m', '-250m', '80%'",
            "time": "'90s', '2 minutes', '+30s'", "plain": "'5', '+1', '-2', '200%'"}


class IntentError(ValueError):
    """The model's operation can't be turned into a valid patch; the message goes back to it."""


def escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def field_kind(path: str) -> str:
    for suffix, kind in (("MiB", "memory"), ("MilliCPU", "cpu"), ("Seconds", "time")):
        if path.endswith(suffix):
            return kind
    return "plain"


def leaf_paths(values: dict) -> dict[str, list[str]]:
    """Existing paths grouped by what may be written there: numbers, text, flags, maps."""
    groups: dict[str, list[str]] = {"number": [], "text": [], "flag": [], "map": []}

    def walk(node, path):
        if path.rsplit("/", 1)[-1] in MAP_CONTAINERS and isinstance(node, dict):
            groups["map"].append(path)
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}/{escape(k)}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")
        elif isinstance(node, bool):  # before int: bool is an int subclass
            groups["flag"].append(path)
        elif isinstance(node, int | float):
            groups["number"].append(path)
        elif isinstance(node, str):
            groups["text"].append(path)

    walk(values, "")
    return groups


def intent_format(values: dict) -> dict:
    """JSON Schema for Ollama's `format`: every path is an enum of paths that exist, by type."""
    paths = leaf_paths(values)
    enum = lambda items: {"type": "string", "enum": items}  # noqa: E731
    shapes = []
    # One familiar "replace" op, with the value type pinned per path. Number fields also take a
    # quantity string ("5gb", "80%", "+1") so the model copies the user's words and code converts.
    for group, value in (("text", {"type": "string"}), ("flag", {"type": "boolean"}),
                         ("number", {"anyOf": [{"type": "number"}, {"type": "string"}]})):
        if paths[group]:
            # JSON Patch's own op names: a small model already knows what "replace" and "add" mean
            shapes.append({
                "type": "object",
                "properties": {"op": enum(["replace"]), "path": enum(paths[group]), "value": value},
                "required": ["op", "path", "value"],
            })
    if paths["map"]:
        # a new map entry is its own shape with "key" required, or the model leaves it out
        shapes.append({
            "type": "object",
            "properties": {"op": enum(["add"]), "path": enum(paths["map"]), "key": {"type": "string"},
                           "value": {"type": "string"}},
            "required": ["op", "path", "key", "value"],
        })
    removable = paths["number"] + paths["text"] + paths["flag"]
    if removable:
        shapes.append({
            "type": "object",
            "properties": {"op": enum(["remove"]), "path": enum(removable)},
            "required": ["op", "path"],
        })
    # wrapped in an object: top-level array schemas are less reliable across Ollama versions
    return {
        "type": "object",
        # maxItems bounds a looping model in the grammar itself instead of at the token limit
        "properties": {"operations": {"type": "array", "items": {"anyOf": shapes}, "maxItems": 8}},
        "required": ["operations"],
    }


def to_field_unit(kind: str, amount: float, unit: str) -> float:
    if kind == "cpu" and unit == "":
        # Kubernetes reads a bare CPU number as cores ("cpu: 2"); large bare numbers are clearly millicores
        # ponytail: threshold heuristic, ask the user back if it ever guesses wrong in practice
        return amount * 1000 if amount <= 64 else amount
    factors = UNITS[kind]
    if unit not in factors:
        raise IntentError(f"unit {unit!r} doesn't fit a {kind} field (write e.g. {EXAMPLES[kind]})")
    return amount * factors[unit]


def resolve_number(path: str, raw, current) -> int | float:
    """A number field's new value from what the model wrote: 5, "5gb", "+512mb", "80%"..."""
    kind = field_kind(path)
    if isinstance(raw, bool):
        raise IntentError(f"{path} needs a number, got {raw!r}")
    if isinstance(raw, int | float):
        if not math.isfinite(raw):
            raise IntentError(f"{path} needs a finite number, got {raw!r}")
        sign, amount, unit = "", raw, ""
    else:
        match = QUANTITY.match(str(raw))
        if not match:
            raise IntentError(f"can't read {raw!r} as a quantity for {path} (write e.g. {EXAMPLES[kind]})")
        sign, amount, unit = match.group(1), float(match.group(2)), match.group(3).lower()
    if (sign or unit == "%") and not isinstance(current, int | float):
        raise IntentError(f"{path} has no current number to change relative to")

    if unit == "%":  # "80%" = 80% of the current value; "+25%" / "-25%" = up / down by 25%
        result = current * amount / 100 if not sign else current * (1 + (amount if sign == "+" else -amount) / 100)
    else:
        delta = to_field_unit(kind, amount, unit)
        result = delta if not sign else current + delta if sign == "+" else current - delta

    # keep integer fields integer; round half up so 1250.5 -> 1251, not banker's rounding
    if current is None or isinstance(current, int):
        return math.floor(result + 0.5)
    return result


def compile_intents(intents, values: dict) -> list[dict]:
    """Turn the model's operations into an RFC 6902 patch, or raise IntentError explaining why not."""
    if not isinstance(intents, list):
        raise IntentError('expected {"operations": [...]}')
    patch = []
    for intent in intents:
        if not isinstance(intent, dict) or not isinstance(intent.get("path"), str):
            raise IntentError(f"each operation needs a string 'path': {intent!r}")
        op, path = intent.get("op"), intent["path"]
        try:
            if op == "replace":
                if "value" not in intent:
                    raise IntentError(f"replace needs a 'value': {intent!r}")
                current = jsonpointer.resolve_pointer(values, path, None)
                value = intent["value"]
                if isinstance(current, int | float) and not isinstance(current, bool):
                    value = resolve_number(path, value, current)  # units and arithmetic happen here
                patch.append({"op": "replace", "path": path, "value": value})
            elif op == "add":
                if not isinstance(intent.get("key"), str) or not intent["key"].strip():
                    raise IntentError(f"add needs a non-empty 'key' (the new env var name): {intent!r}")
                patch.append({"op": "add", "path": f"{path}/{escape(intent['key'].strip())}",
                              "value": intent.get("value", "")})
            elif op == "remove":
                patch.append({"op": "remove", "path": path})
            else:
                raise IntentError(f"unknown op {op!r}")
        except KeyError as e:
            raise IntentError(f"{op} is missing {e}: {intent!r}") from e
    return patch


if __name__ == "__main__":
    values = {"cpu": {"limitMilliCPU": 1500}, "mem": {"limitMiB": 2048}, "t": {"readSeconds": 60}, "replicas": 2}
    for path, raw, expected in [
        ("/mem/limitMiB", "5gb", 5120), ("/mem/limitMiB", "+512mb", 2560), ("/mem/limitMiB", 3072, 3072),
        ("/cpu/limitMilliCPU", "80%", 1200), ("/cpu/limitMilliCPU", "2.5 cores", 2500),
        ("/cpu/limitMilliCPU", "750m", 750), ("/cpu/limitMilliCPU", "+25%", 1875), ("/cpu/limitMilliCPU", "2", 2000),
        ("/t/readSeconds", "2 minutes", 120), ("/replicas", "-1", 1), ("/replicas", "200%", 4),
    ]:
        got = resolve_number(path, raw, jsonpointer.resolve_pointer(values, path))
        assert got == expected, (path, raw, got)
    print("intents self-check ok")
