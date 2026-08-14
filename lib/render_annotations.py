# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Emit audit findings as GitHub workflow annotations.

Reads the JSON report on stdin and writes workflow commands on stdout so
that each finding appears against the run rather than only inside the
step log.
"""

from __future__ import annotations

import json
import sys
from typing import cast

#: Maps an audit severity onto the matching workflow command.
COMMANDS = {
    "error": "error",
    "warning": "warning",
    "notice": "notice",
}


def escape_data(value: str) -> str:
    """Escape the message portion of a workflow command.

    GitHub treats the percent sign and line breaks as control characters
    inside a command, so replace them with their documented escapes.
    """
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape a property value such as ``title`` or ``file``.

    A property list separates entries with commas and each key from its
    value with a colon, so both need escaping on top of the rules that
    apply to the message.
    """
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def command_for(entry: dict[str, object]) -> str | None:
    """Build a workflow command for one finding, or None to skip it."""
    command = COMMANDS.get(str(entry.get("severity", "")))
    if command is None:
        return None

    title = escape_property(f"RTD audit: {entry.get('code', '')}")
    message = escape_data(str(entry.get("message", "")))
    path = entry.get("path")
    location = f",file={escape_property(str(path))}" if path else ""
    return f"::{command} title={title}{location}::{message}"


def render(report: dict[str, object]) -> list[str]:
    """Build a workflow command for every finding."""
    raw = report.get("findings")
    if not isinstance(raw, list):
        return []

    commands: list[str] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        command = command_for(cast("dict[str, object]", item))
        if command is not None:
            commands.append(command)
    return commands


def main() -> int:
    """Read a report from stdin and print its annotations."""
    try:
        loaded = cast("object", json.load(sys.stdin))
    except json.JSONDecodeError:
        return 0
    if not isinstance(loaded, dict):
        return 0
    for line in render(cast("dict[str, object]", loaded)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
