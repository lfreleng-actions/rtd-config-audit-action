# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Shared types and helpers for the ReadTheDocs configuration audit."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

#: Configuration file names Read the Docs accepts, in search order.
CONFIG_NAMES = (".readthedocs.yaml", ".readthedocs.yml")

#: Tox files that may hold the documentation environment, in search order.
TOX_NAMES = ("docs/tox.ini", "tox.ini")

#: Keys Read the Docs no longer honours, mapped to the advice we give.
RETIRED_KEYS = {
    "build.image": "Read the Docs ignores 'build.image'; use 'build.os' and 'build.tools' instead.",
    "python.version": "Read the Docs ignores 'python.version'; use 'build.tools.python' instead.",
}

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_NOTICE = "notice"


@dataclass
class Finding:
    """A single audit result."""

    severity: str
    code: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Render the finding for JSON output."""
        payload: dict[str, object] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass
class Audit:
    """Accumulated findings."""

    findings: list[Finding] = field(default_factory=list)

    def add(
        self, severity: str, code: str, message: str, path: str | None = None
    ) -> None:
        """Record a finding."""
        self.findings.append(Finding(severity, code, message, path))

    def count(self, severity: str) -> int:
        """Count findings of a given severity."""
        return sum(1 for f in self.findings if f.severity == severity)


def split_list(raw: str) -> list[str]:
    """Split a newline or comma separated input into entries.

    Blank entries and surrounding whitespace disappear, so an operator can
    format the input as a YAML block scalar without surprises.
    """
    parts = re.split(r"[\n,]+", raw or "")
    return [p.strip() for p in parts if p.strip()]


def normalise_python(value: object) -> str:
    """Reduce a Python version token to a bare ``X.Y`` string.

    Accepts the forms tox and Read the Docs use: ``3.13``, ``python3.13``,
    ``py313`` and ``py3.13``. Returns an empty string for a generic
    interpreter such as ``py3`` or ``python3``, which pins nothing.
    """
    text = str(value).strip().strip("\"'").lower()
    text = text.removeprefix("python").removeprefix("py")
    if re.fullmatch(r"\d+\.\d+", text):
        return text
    if re.fullmatch(r"\d{2,3}", text):
        return f"{text[0]}.{text[1:]}"
    return ""


def find_first(root: Path, names: tuple[str, ...]) -> Path | None:
    """Return the first existing path among ``names`` under ``root``."""
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def nested_get(data: dict[str, object], dotted: str) -> object:
    """Fetch a nested mapping value using a dotted key path.

    Returns ``None`` when any level is absent or is not a mapping, so a
    caller never has to guard each step.
    """
    parts = dotted.split(".")
    mapping: dict[str, object] = data

    for part in parts[:-1]:
        branch = mapping.get(part)
        if not isinstance(branch, dict):
            return None
        mapping = cast("dict[str, object]", branch)

    return mapping.get(parts[-1])


def relative_to(path: Path, root: Path) -> str:
    """Render a path relative to the project root when possible."""
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def resolve_under(root: Path, value: str) -> Path:
    """Resolve a caller-supplied path against the project root.

    An absolute path stands on its own. A relative path joins the
    project root rather than the working directory, so a caller naming
    ``docs/tox.ini`` means the file inside the project it pointed at,
    whatever directory the action happens to run from.
    """
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate
