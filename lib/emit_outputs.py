# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Translate an audit report into GitHub Actions output assignments.

Reads the JSON report on stdin and writes ``key=value`` lines on stdout,
which the calling action appends to ``$GITHUB_OUTPUT``. The complete
report travels as a heredoc block so that its newlines cannot inject
further keys.
"""

from __future__ import annotations

import json
import secrets
import sys
from typing import cast

#: Outputs the action exposes as plain scalars.
SCALAR_KEYS = (
    "passed",
    "config_found",
    "config_path",
    "tox_path",
    "tox_dir",
    "docs_python",
    "tox_python",
    "rtd_python",
    "python_mismatch",
    "python_eol",
    "build_os",
    "errors",
    "warnings",
)


def scalar(value: object) -> str:
    """Render a report value as a single-line output string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\n", " ").replace("\r", " ")


def heredoc(name: str, payload: str) -> list[str]:
    """Emit a multi-line output using a delimiter absent from the payload."""
    delimiter = f"ghadelim_{secrets.token_hex(16)}"
    while delimiter in payload:
        delimiter = f"ghadelim_{secrets.token_hex(16)}"
    return [f"{name}<<{delimiter}", payload, delimiter]


def render(report: dict[str, object], raw: str) -> list[str]:
    """Build every output assignment for a report."""
    lines = [f"{key}={scalar(report.get(key))}" for key in SCALAR_KEYS]
    lines += heredoc("findings_json", raw)
    return lines


def main() -> int:
    """Read a report from stdin and print its output assignments."""
    raw = sys.stdin.read()
    try:
        loaded = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        _ = sys.stderr.write("Error: the audit produced no readable report\n")
        return 1
    if not isinstance(loaded, dict):
        _ = sys.stderr.write("Error: the audit report is not an object\n")
        return 1

    for line in render(cast("dict[str, object]", loaded), raw):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
