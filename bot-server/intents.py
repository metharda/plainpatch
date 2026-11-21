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
    # "m" = MiB: the model writes "512m" for half a gig even when told not to, and Kubernetes' literal
    # reading (milli-bytes) is meaningless for a memory limit. Measured: rejecting it only caused failures.
    "memory": {"": 1, "m": 1, "mb": 1, "mi": 1, "mib": 1, "g": 1024, "gb": 1024, "gi": 1024, "gib": 1024},
    "cpu": {"m": 1, "millicore": 1, "millicores": 1, "millicpu": 1,
            "core": 1000, "cores": 1000, "cpu": 1000, "cpus": 1000},  # bare numbers: see to_field_unit
    "time": {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
             "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60},
    "plain": {"": 1},
}
# "+512mb", "-1", "2.5 cores", "80%"
QUANTITY = re.compile(r"^\s*([+-]?)\s*(\d*\.?\d+)\s*([a-z%]*)\s*$", re.IGNORECASE)
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
        # Kubernetes reads a bare CPU number as cores ("cpu: 2"); 100+ is clearly millicores ("1500").
        # In between ("65") either reading could be a 1000x mistake, so ask instead of guessing.
        if amount <= 64:
            return amount * 1000
        if amount >= 100:
            return amount
        raise IntentError(f"bare CPU value {amount:g} is ambiguous; write '{amount:g}m' or '{amount:g} cores'")
    factors = UNITS[kind]
    if unit not in factors:
        raise IntentError(f"unit {unit!r} doesn't fit a {kind} field (write e.g. {EXAMPLES[kind]})")
    return amount * factors[unit]


RELATIVE_CUES = re.compile(r"\+|\b(by|increase|decrease|reduce|raise|lower|up|down|more|less|add|subtract|bump|"
                           r"cut|grow|shrink|double|triple|halve|half)\b", re.IGNORECASE)


def parse_quantity(kind: str, raw) -> tuple[str, float, str]:
    if isinstance(raw, bool):
        raise IntentError(f"needs a number, got {raw!r}")
    if isinstance(raw, int | float):
        if not math.isfinite(raw):
            raise IntentError(f"needs a finite number, got {raw!r}")
        return "", raw, ""
    match = QUANTITY.match(str(raw))
    if not match:
        raise IntentError(f"can't read {raw!r} as a quantity (write e.g. {EXAMPLES[kind]})")
    return match.group(1), float(match.group(2)), match.group(3).lower()


def resolve_number(path: str, raw, current, request: str | None = None) -> int | float:
    """A number field's new value from what the model wrote: 5, "5gb", "+512mb", "80%"...

    A sign means "relative" only when the request words it that way ("down by 1"); "set replicas
    to -1" stays an absolute -1, which the schema then refuses.
    """
    kind = field_kind(path)
    try:
        sign, amount, unit = parse_quantity(kind, raw)
    except IntentError as e:
        raise IntentError(f"{path}: {e}") from e
    if sign and request is not None and not RELATIVE_CUES.search(request):
        amount, sign = -amount if sign == "-" else amount, ""
    if (sign or unit == "%") and not isinstance(current, int | float):
        raise IntentError(f"{path} has no current number to change relative to")

    if unit == "%":  # "80%" = 80% of the current value; "+25%" / "-25%" = up / down by 25%
        result = current * amount / 100 if not sign else current * (1 + (amount if sign == "+" else -amount) / 100)
    else:
        delta = to_field_unit(kind, amount, unit)
        result = delta if not sign else current + delta if sign == "+" else current - delta

    # keep integer fields integer: nearest whole number, ties away from the current value,
    # so a requested change never rounds back into a no-op (2 replicas "down by 25%" -> 1, not 2)
    if current is None or isinstance(current, int):
        if not sign and unit != "%" and result != int(result):
            raise IntentError(f"{path} takes a whole number, got {raw!r}")
        down = current is not None and result < current
        return math.ceil(result - 0.5) if down else math.floor(result + 0.5)
    return result


# Grounding: everything the model writes must come from the request. These catch a small model
# inventing values ("cpu to 65" -> "6500"), env vars (MEMORY="3gb"), or edits for vague requests.
NUMBER = re.compile(r"\d*\.?\d+")
ENV_WORDS = re.compile(r"\b(env|envs|environment|variables?|vars?)\b", re.IGNORECASE)
RELATIVE_WORDS = re.compile(r"%|\b(double|twice|triple|half|halve|quarter|percent)\b", re.IGNORECASE)
NUMBER_WORDS = re.compile(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|half|dozen|hundred)\b",
                          re.IGNORECASE)
FLAG_WORDS = re.compile(r"\b(true|false|enabled?|disabled?|enable|disable|on|off|yes|no)\b", re.IGNORECASE)


REQUEST_QUANTITY = re.compile(r"(\d*\.?\d+)\s*([a-z%]*)", re.IGNORECASE)
WORD_FACTORS = {"double": 2, "twice": 2, "triple": 3, "halve": 0.5, "half": 0.5, "quarter": 0.25}


def number_candidates(kind: str, current, request: str) -> set[float]:
    """Every value the request's numbers could mean for this field: the amount itself, +/- it,
    a percentage of or by it, or a doubling/halving. Lets the model do arithmetic, but checks it."""
    out: set[float] = set()
    for number, word in REQUEST_QUANTITY.findall(request.lower()):
        n = float(number)
        if word == "%":
            if isinstance(current, int | float):
                out |= {current * n / 100, current * (1 + n / 100), current * (1 - n / 100)}
            continue
        for unit in dict.fromkeys([word, ""]):  # "2 cores" -> cores; "3 replicas" -> bare 3
            try:
                q = to_field_unit(kind, n, unit)
            except IntentError:
                continue
            out |= {q, -q}
            if isinstance(current, int | float):
                out |= {current + q, current - q}
            break
    if isinstance(current, int | float):
        out |= {current * f for w, f in WORD_FACTORS.items() if re.search(rf"\b{w}\b", request, re.IGNORECASE)}
    return out


def grounded(value, request: str) -> bool:
    """Does `value`, as the model wrote it, come from the user's words?"""
    if isinstance(value, bool):
        return bool(FLAG_WORDS.search(request))
    raw, text = str(value).lower(), request.lower()
    if re.sub(r"\s+", "", raw) in re.sub(r"\s+", "", text):
        return True
    numbers = [float(n) for n in NUMBER.findall(raw)]
    if numbers and all(n in {float(m) for m in NUMBER.findall(text)} for n in numbers):
        return True  # "512 MiB" for "512mb", "+1" for "up by 1"
    if raw.endswith("%") and RELATIVE_WORDS.search(text):
        return True  # "200%" for "double"
    return bool(numbers) and bool(NUMBER_WORDS.search(text))  # "1.5 cores" for "one and a half cores"


def check_grounding(op: str, path: str, intent: dict, request: str):
    if op == "add":
        key = intent["key"].strip()
        if not ENV_WORDS.search(request):
            raise IntentError("only add env vars when the request asks for one; change existing fields with replace")
        if not re.search(rf"(?<!\w){re.escape(key)}(?!\w)", request, re.IGNORECASE):
            raise IntentError(f"only add keys the request names; {key!r} isn't in it")
        value = str(intent.get("value", "")).strip()
        if not value:
            raise IntentError(f"a new env var needs a value from the request; {key!r} has none")
        if key.lower() == value.lower():
            raise IntentError(f"{key!r} is the value, not the variable name")
    if op == "add" and not grounded(intent.get("value", ""), request):
        raise IntentError(f"value {intent.get('value')!r} isn't in the request; copy values exactly as the user wrote them")


def compile_intents(intents, values: dict, request: str | None = None) -> tuple[list[dict], list[str]]:
    """Turn the model's operations into (RFC 6902 patch, paths already at the requested value).

    With `request`, every key and value must be grounded in it (see check_grounding).
    Raises IntentError, whose message goes back to the model as retry feedback.
    """
    if not isinstance(intents, list):
        raise IntentError('expected {"operations": [...]}')
    patch, dropped, padding = [], [], 0
    for intent in intents:
        if not isinstance(intent, dict) or not isinstance(intent.get("path"), str):
            raise IntentError(f"each operation needs a string 'path': {intent!r}")
        op, path = intent.get("op"), intent["path"]
        try:
            if op in ("replace", "add") and "value" not in intent:
                raise IntentError(f"{op} needs a 'value': {intent!r}")
            if op == "add" and (not isinstance(intent.get("key"), str) or not intent["key"].strip()):
                raise IntentError(f"add needs a non-empty 'key' (the new env var name): {intent!r}")
            if op in ("replace", "add", "remove") and request is not None:
                check_grounding(op, path, intent, request)

            if op == "replace":
                if request is not None and is_unrequested_request_field(path, request):
                    dropped.append(path)  # "memory" means the limit; any needed request change is clamped below
                    continue
                current = jsonpointer.resolve_pointer(values, path, None)
                value = intent["value"]
                if isinstance(current, int | float) and not isinstance(current, bool):
                    value = resolve_number(path, value, current, request)  # units and arithmetic happen here
                    ok = request is None or follows_from(value, path, current, request)
                else:
                    ok = request is None or grounded(value, request)
                if not ok and value == current:
                    padding += 1
                    continue  # padding: an unrequested field "set" to its current value; harmless, drop it
                if (ok and isinstance(value, str) and not isinstance(current, int | float)
                        and looks_like_quantity(value)):
                    raise IntentError(f"{value!r} looks like a quantity, but {path} is a text field; "
                                      f"use the number field it belongs to (…MiB, …MilliCPU, …Seconds)")
                if not ok and isinstance(value, int | float) and not isinstance(value, bool):
                    raise IntentError(f"{intent['value']!r} ({value:g}) doesn't follow from the numbers in the "
                                      f"request; copy quantities exactly as the user wrote them")
                if not ok:
                    raise IntentError(f"value {value!r} isn't in the request; copy values exactly as the user wrote them")
                patch.append({"op": "replace", "path": path, "value": value})
            elif op == "add":
                patch.append({"op": "add", "path": f"{path}/{escape(intent['key'].strip())}", "value": intent["value"]})
            elif op == "remove":
                patch.append({"op": "remove", "path": path})
            else:
                raise IntentError(f"unknown op {op!r}")
        except KeyError as e:
            raise IntentError(f"{op} is missing {e}: {intent!r}") from e
    if dropped and not patch:
        raise IntentError(f"the request doesn't mention resource requests; change the limit, not {dropped[0]}")
    if padding and not patch:
        raise IntentError("none of these operations match the request; change the fields and values it names")
    kept, unchanged = split_no_ops(patch, values)
    return kept + clamp_requests(kept, values), unchanged


QUANTITY_UNITS = {u for kind in ("memory", "cpu") for u in UNITS[kind]} - {""}


def looks_like_quantity(value: str) -> bool:
    """'3gb' or '1.5 cores' aimed at a text field is a number in the wrong place ('25%' or '1.0' aren't)."""
    match = QUANTITY.match(value)
    return bool(match) and match.group(3).lower() in QUANTITY_UNITS


def is_unrequested_request_field(path: str, request: str) -> bool:
    return path.rsplit("/", 1)[-1].startswith("request") and not re.search(r"\brequests?\b", request, re.IGNORECASE)


def follows_from(value, path: str, current, request: str) -> bool:
    if NUMBER_WORDS.search(request):  # "one and a half cores": trust the model's reading of number words
        return True
    return any(abs(value - c) <= 0.5 for c in number_candidates(field_kind(path), current, request))


def clamp_requests(patch: list[dict], values: dict) -> list[dict]:
    """Kubernetes rejects a limit below its request. When a lowered limit crosses the request the user
    didn't mention, lower the request to match (it shows up in `changes`, so nothing is hidden)."""
    extra, touched = [], {op["path"] for op in patch}
    for op in patch:
        parent, _, field = op["path"].rpartition("/")
        if op["op"] != "replace" or not field.startswith("limit"):
            continue
        request_path = f"{parent}/request{field[len('limit'):]}"
        current_request = jsonpointer.resolve_pointer(values, request_path, None)
        if (request_path not in touched and isinstance(current_request, int | float)
                and isinstance(op["value"], int | float) and op["value"] < current_request):
            extra.append({"op": "replace", "path": request_path, "value": op["value"]})
    return extra


def split_no_ops(patch: list[dict], values: dict) -> tuple[list[dict], list[str]]:
    """Drop repeats and ops that set a field to its current value; report the latter as unchanged."""
    kept, unchanged, seen = [], [], {}
    for op in patch:
        value = op.get("value", _MISSING)
        if seen.setdefault(op["path"], value) != value:
            raise IntentError(f"conflicting values for {op['path']}; set each field once")
        current = jsonpointer.resolve_pointer(values, op["path"], _MISSING)
        if op["op"] in ("replace", "add") and current == value and type(current) is type(value):
            if op["path"] not in unchanged:
                unchanged.append(op["path"])
        elif op not in kept:
            kept.append(op)
    return kept, unchanged


_MISSING = object()


def check_limits(config) -> None:
    """A resource limit below its request is rejected by Kubernetes; the JSON Schemas can't express it."""
    if isinstance(config, dict):
        for key, limit in config.items():
            if key.startswith("limit") and isinstance(limit, int | float):
                request = config.get("request" + key[len("limit"):])
                if isinstance(request, int | float) and limit < request:
                    raise IntentError(f"{key} ({limit:g}) would be below {'request' + key[5:]} ({request:g}); "
                                      f"lower the request too, or pick a higher limit")
        for value in config.values():
            check_limits(value)
    elif isinstance(config, list):
        for value in config:
            check_limits(value)


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
