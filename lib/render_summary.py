# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Render an audit report as a GitHub step summary.

Reads the JSON report on stdin and writes Markdown on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import cast

SEVERITY_ICON = {
    "error": "❌",
    "warning": "⚠️",
    "notice": "ℹ️",
}

HEADING = "### ReadTheDocs configuration audit"


def text_of(report: dict[str, object], key: str, fallback: str = "") -> str:
    """Read a report field as text, substituting a fallback when empty."""
    value = report.get(key)
    if value is None or value == "":
        return fallback
    return str(value)


def property_rows(report: dict[str, object]) -> list[str]:
    """Build the property table for a report."""
    return [
        "| Property | Value |",
        "| -------- | ----- |",
        f"| Build image | `{text_of(report, 'build_os', 'not declared')}` |",
        f"| ReadTheDocs Python | `{text_of(report, 'rtd_python', 'not declared')}` |",
        f"| tox Python | `{text_of(report, 'tox_python', 'not pinned')}` |",
        f"| Resolved docs Python | `{text_of(report, 'docs_python', 'unknown')}` |",
        f"| Errors | {text_of(report, 'errors', '0')} |",
        f"| Warnings | {text_of(report, 'warnings', '0')} |",
        "",
    ]


def finding_rows(report: dict[str, object]) -> list[str]:
    """Build the findings table for a report."""
    raw = report.get("findings")
    if not isinstance(raw, list) or not raw:
        return ["No findings.", ""]

    rows = ["| | Finding |", "| --- | ------- |"]
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        icon = SEVERITY_ICON.get(str(entry.get("severity", "")), "")
        message = str(entry.get("message", "")).replace("|", "\\|")
        rows.append(f"| {icon} | {message} |")
    rows.append("")
    return rows


def render(report: dict[str, object]) -> str:
    """Build the Markdown summary for a report."""
    if not report.get("config_found"):
        return "\n".join([HEADING, "", "No ReadTheDocs configuration found.", ""])

    outcome = "passed ✅" if report.get("passed") else "failed ❌"
    lines = [
        HEADING,
        "",
        f"Audit {outcome} for `{text_of(report, 'config_path')}`.",
        "",
    ]
    lines += property_rows(report)
    lines += finding_rows(report)
    return "\n".join(lines)


def main() -> int:
    """Read a report from stdin and print its summary."""
    try:
        loaded = cast("object", json.load(sys.stdin))
    except json.JSONDecodeError:
        print(f"{HEADING}\n\nThe audit produced no readable report.\n")
        return 0
    if not isinstance(loaded, dict):
        print(f"{HEADING}\n\nThe audit produced no readable report.\n")
        return 0
    print(render(cast("dict[str, object]", loaded)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
