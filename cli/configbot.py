#!/usr/bin/env python3
"""Terminal client for the bot service. Standard library only.

    configbot "set storefront memory to 1gb"            preview the change as a diff
    configbot "set storefront memory to 1gb" --apply    preview and save it
    configbot history storefront                        list saved changes
    configbot undo storefront                           roll back the latest change
"""
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BOT_URL = os.getenv("CONFIGBOT_URL", "http://localhost:7000")
SYMBOLS = {"add": ("+", "32"), "remove": ("-", "31"), "replace": ("~", "33")}


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
            detail = json.load(e).get("detail", e.reason)
        except ValueError:
            detail = e.reason
        sys.exit(f"error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {BOT_URL}: {e.reason}")


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
    if not result["changes"]:
        print("  (no changes)")
    for change in result["changes"]:
        print(render_change(change))
    if result["applied"]:
        print(color(f"saved as revision #{result['revision']}", "32"))
    elif result["changes"]:
        print(color("preview only, re-run with --apply to save", "2"))


def cmd_history(args):
    entries = call("GET", f"/history/{args.app}?{urllib.parse.urlencode({'limit': args.limit})}")
    if not entries:
        print("no history yet")
    for e in entries:
        print(color(f"#{e['id']}  {e['timestamp']}  {e['author']}", "1") + f"  {e['message']}")
        for op in e["patch"]:
            print(render_change(op))


def cmd_undo(args):
    entry = call("POST", f"/undo/{args.app}?{urllib.parse.urlencode({'author': args.author})}")
    print(color(f"reverted #{entry['reverts']} as revision #{entry['id']}", "32"))
    for op in entry["patch"]:
        print(render_change(op))


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv and argv[0] in ("history", "undo") else None

    parser = argparse.ArgumentParser(prog=f"configbot {command}" if command else "configbot")
    parser.add_argument("--author", default=getpass.getuser(), help="name recorded in the audit log")
    if command:
        parser.add_argument("app")
        if command == "history":
            parser.add_argument("--limit", type=int, default=20)
        argv = argv[1:]
    else:
        parser.add_argument("request", help='e.g. "set storefront memory to 1gb"')
        parser.add_argument("--apply", action="store_true", help="save the change instead of previewing it")

    args = parser.parse_args(argv)
    {"history": cmd_history, "undo": cmd_undo, None: cmd_message}[command](args)


if __name__ == "__main__":
    main()
