# SPDX-License-Identifier: GPL-3.0-only
"""The command line: `winlight-cli` (or `python main.py` with these options).

It asks the running app over the single-instance pipe and prints the
answer, so scripts, AutoHotkey, Task Scheduler or a Stream Deck can control
brightness and night light. Like Twinkle Tray's, it needs Winlight running.

Exit codes: 0 done, 1 the request failed (e.g. no such display),
2 Winlight isn't running.
"""

from __future__ import annotations

import argparse
import json
import sys

from nightlight import APP_NAME
from nightlight.single_instance import SingleInstance

EXAMPLES = """\
examples:
  winlight-cli --list                       displays, their numbers and brightness
  winlight-cli --set 40                     every display to 40%
  winlight-cli --set +10 --display 2        display 2 up by 10
  winlight-cli --set 70 --display DELL      by name (any part of it) or id
  winlight-cli --get --display 1            prints just the number, for scripts
  winlight-cli --night-light toggle         also on, off, auto (follow the schedule), status
  winlight-cli --list --json                for scripts
"""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("command line (needs Winlight running)")
    group.add_argument("--list", action="store_true", help="list displays with their numbers, ids and brightness")
    group.add_argument("--get", action="store_true", help="print the brightness of --display (default: all)")
    group.add_argument("--set", metavar="VALUE", help="set brightness: 0-100, or +N/-N to change it")
    group.add_argument("--display", default="all", metavar="WHICH", help="a number from --list, an id, part of a name, or all")
    group.add_argument("--night-light", choices=("on", "off", "toggle", "auto", "status"), help="night light: on/off until changed back, or auto")
    group.add_argument("--refresh", action="store_true", help="detect displays again")
    group.add_argument("--json", action="store_true", help="print JSON instead of text")


def requested(args: argparse.Namespace) -> bool:
    return bool(args.list or args.get or args.set is not None or args.night_light or args.refresh)


def build_request(args: argparse.Namespace) -> dict:
    if getattr(args, "diagnostics_request", False):
        return {"command": "diagnostics"}
    if args.list:
        return {"command": "list"}
    if args.set is not None:
        return {"command": "set", "value": args.set, "display": args.display}
    if args.get:
        return {"command": "get", "display": args.display}
    if args.night_light:
        return {"command": "night-light", "action": args.night_light}
    return {"command": "refresh"}


def unicode_output() -> None:
    """Display names can be in any script ("显示器"). A console takes any text,
    but output redirected to a file or a pipe is encoded with the legacy code
    page (cp1252), where that fails: write UTF-8 there, and never crash over
    a character (Python 3.15 does the former by itself)."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def run(args: argparse.Namespace, instance: SingleInstance | None = None) -> int:
    unicode_output()
    reply = (instance or SingleInstance()).request(build_request(args))
    if reply is None:
        print(f"{APP_NAME} isn't running. Start it first, then try again.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": reply.get("ok", False), **(reply.get("data") or {}), "message": reply.get("output", "")}, indent=2))
    elif reply.get("output"):
        print(reply["output"], file=sys.stdout if reply.get("ok") else sys.stderr)
    return 0 if reply.get("ok") else 1
