#!/usr/bin/env python3
"""Terminal client for the bot service. Standard library only.

    configbot "set storefront memory to 1gb"            preview the change as a diff
    configbot "set storefront memory to 1gb" --apply    preview and save it
    configbot history storefront                        list saved changes
    configbot undo storefront                           roll back the latest change
"""
import argparse
import getpass
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BOT_URL = os.getenv("CONFIGBOT_URL", "http://localhost:7000").rstrip("/")
SYMBOLS = {"add": ("+", "32"), "remove": ("-", "31"), "replace": ("~", "33")}
COMMANDS = ("history", "undo")


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def call(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BOT_URL + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
            detail = body.get("detail", e.reason) if isinstance(body, dict) else body
        except (ValueError, OSError, http.client.HTTPException):
            detail = e.reason
        sys.exit(f"error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {BOT_URL}: {e.reason}")
    except (ValueError, OSError, http.client.HTTPException) as e:
        # non-JSON body, truncated response, reset connection...
        sys.exit(f"bad response from {BOT_URL}: {e}")


def app_path(app: str) -> str:
    return urllib.parse.quote(app, safe="")


def default_author() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):  # no USER/LOGNAME and a uid with no passwd entry, e.g. in containers
        return "anonymous"


def render_change(change: dict) -> str:
    op, path = change["op"], change["path"]
    symbol, code = SYMBOLS.get(op, ("*", "36"))
    old, new = json.dumps(change.get("old")), json.dumps(change.get("new", change.get("value")))
    if op == "add":
        line = f"{path} = {new}"
    elif op == "remove":
        line = f"{path}" + (f" (was {old})" if "old" in change else "")
    elif "old" in change:
        line = f"{path}: {old} → {new}"
    else:
        line = f"{path} = {new}"
    return color(f"  {symbol} {line}", code)


def cmd_message(args):
    result = call("POST", "/edits", {"text": args.request, "apply": args.apply, "author": args.author})
    print(color(f"app: {result['app']}", "1"))
    for path in result.get("unchanged", []):
        print(color(f"  = {path} (already set)", "2"))
    if not result["changes"]:
        print("  (no changes)")
    for change in result["changes"]:
        print(render_change(change))
    if result["applied"]:
        print(color(f"saved as revision #{result['revision']}", "32"))
    elif result["changes"]:
        print(color("preview only, re-run with --apply to save", "2"))


def cmd_history(args):
    entries = call("GET", f"/history/{app_path(args.app)}?{urllib.parse.urlencode({'limit': args.limit})}")
    if not entries:
        print("no history yet")
    for e in entries:
        print(color(f"#{e['id']}  {e['timestamp']}  {e['author']}", "1") + f"  {e['message']}")
        for op in e["patch"]:
            print(render_change(op))


def cmd_undo(args):
    entry = call("POST", f"/undo/{app_path(args.app)}?{urllib.parse.urlencode({'author': args.author})}")
    print(color(f"reverted #{entry['reverts']} as revision #{entry['id']}", "32"))
    for op in entry["patch"]:
        print(render_change(op))


def limit(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 500:
        raise argparse.ArgumentTypeError("must be between 1 and 500")
    return value


def find_command(argv: list[str]) -> int | None:
    """Index of the history/undo subcommand: the first positional argument, skipping option values."""
    i = 0
    while i < len(argv):
        if argv[i] in ("--author", "--limit"):
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return i if argv[i] in COMMANDS else None
    return None


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    index = find_command(argv)
    command = argv.pop(index) if index is not None else None

    parser = argparse.ArgumentParser(
        prog=f"configbot {command}" if command else "configbot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--author", default=default_author(), help="name recorded in the audit log")
    if command:
        parser.add_argument("app")
        if command == "history":
            parser.add_argument("--limit", type=limit, default=20, help="1-500 (default 20)")
    else:
        parser.add_argument("request", help='e.g. "set storefront memory to 1gb"')
        parser.add_argument("--apply", action="store_true", help="save the change instead of previewing it")

    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # e.g. the arrow on an ASCII-only terminal
    try:
        {"history": cmd_history, "undo": cmd_undo, None: cmd_message}[command](args)
    except (KeyError, TypeError, AttributeError) as e:  # a 200 whose body isn't what we expect
        sys.exit(f"bad response from {BOT_URL}: unexpected shape ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
