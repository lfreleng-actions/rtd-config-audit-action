# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Individual checks that make up the ReadTheDocs configuration audit.

Each function records its findings on the supplied :class:`Audit` and
returns whatever value the caller needs, so the orchestration in
:mod:`rtd_audit` stays readable.
"""

from __future__ import annotations

import configparser
import re
import sys
from pathlib import Path
from typing import cast

from lib.rtd_model import (
    RETIRED_KEYS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Audit,
    nested_get,
    normalise_python,
    relative_to,
)

try:
    import yaml
except ImportError:  # pragma: no cover - handled by the calling action
    _ = sys.stderr.write(
        "Error: PyYAML is required. The calling action installs it; install it "
        + "manually when running this script directly.\n"
    )
    raise SystemExit(2) from None

#: Matches a sphinx-build invocation inside a tox command list.
SPHINX_BUILD = re.compile(r"\bsphinx-build\b")

#: Matches an explicit warnings-as-errors flag.
DASH_W = re.compile(r"(?:^|\s)-W(?:\s|$)")

#: Matches a linkcheck builder selection.
LINKCHECK_BUILDER = re.compile(r"-b\s+linkcheck\b")


def load_config(path: Path, audit: Audit) -> dict[str, object]:
    """Parse the Read the Docs configuration file.

    Records an error and returns an empty mapping when the document fails
    to parse or does not hold a mapping at the top level.
    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as exc:
        audit.add(
            SEVERITY_ERROR,
            "config-unparsable",
            f"Cannot parse as YAML: {exc}",
            path.name,
        )
        return {}
    except OSError as exc:
        audit.add(
            SEVERITY_ERROR, "config-unreadable", f"Cannot read file: {exc}", path.name
        )
        return {}

    if raw is None:
        audit.add(
            SEVERITY_ERROR, "config-empty", "Configuration file is empty", path.name
        )
        return {}
    if not isinstance(raw, dict):
        audit.add(
            SEVERITY_ERROR,
            "config-not-mapping",
            "Top level of the configuration must be a mapping",
            path.name,
        )
        return {}
    return cast("dict[str, object]", raw)


def check_version_key(config: dict[str, object], location: str, audit: Audit) -> None:
    """Check the configuration declares schema version 2."""
    version = config.get("version")
    if version is None:
        audit.add(
            SEVERITY_ERROR,
            "version-missing",
            "Configuration does not declare 'version'",
            location,
        )
    elif str(version) != "2":
        audit.add(
            SEVERITY_ERROR,
            "version-unsupported",
            f"Read the Docs supports schema version 2; found {version!r}",
            location,
        )


def check_retired_keys(config: dict[str, object], location: str, audit: Audit) -> None:
    """Report keys Read the Docs no longer honours."""
    for dotted, advice in RETIRED_KEYS.items():
        if nested_get(config, dotted) is not None:
            audit.add(SEVERITY_WARNING, "retired-key", advice, location)


def check_build_os(
    config: dict[str, object], location: str, expected: str, audit: Audit
) -> str:
    """Check the declared build image, returning whatever the file declares."""
    declared = nested_get(config, "build.os")
    if declared is None:
        audit.add(
            SEVERITY_ERROR,
            "build-os-missing",
            "Configuration does not declare 'build.os'",
            location,
        )
        return ""

    declared_text = str(declared)
    if expected and declared_text != expected:
        audit.add(
            SEVERITY_WARNING,
            "build-os-mismatch",
            f"'build.os' is {declared_text!r}; the configured policy expects {expected!r}",
            location,
        )
    return declared_text


def check_rtd_python(
    config: dict[str, object], location: str, expected: str, audit: Audit
) -> str:
    """Check the declared documentation interpreter, returning its version."""
    declared = nested_get(config, "build.tools.python")
    if declared is None:
        audit.add(
            SEVERITY_ERROR,
            "python-missing",
            "Configuration does not declare 'build.tools.python'",
            location,
        )
        return ""

    version = normalise_python(declared)
    if expected and version != normalise_python(expected):
        audit.add(
            SEVERITY_WARNING,
            "python-policy-mismatch",
            f"'build.tools.python' is {version!r}; the configured policy expects {normalise_python(expected)!r}",
            location,
        )
    return version


def check_files(
    root: Path, required: list[str], forbidden: list[str], audit: Audit
) -> None:
    """Check the presence and absence of project files named by policy."""
    for name in required:
        if not (root / name).exists():
            audit.add(
                SEVERITY_ERROR,
                "required-file-missing",
                f"Required file is absent: {name}",
                name,
            )
    for name in forbidden:
        if (root / name).exists():
            audit.add(
                SEVERITY_ERROR,
                "forbidden-file-present",
                f"File should no longer exist: {name}",
                name,
            )


def read_tox_docs_python(tox_path: Path, root: Path, audit: Audit) -> str:
    """Extract ``basepython`` from the tox documentation environment.

    Looks in ``[testenv:docs]`` and falls back to ``[testenv]``. Returns an
    empty string when neither pins an interpreter.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(tox_path.read_text(encoding="utf-8"))
    except (configparser.Error, OSError) as exc:
        audit.add(
            SEVERITY_WARNING,
            "tox-unparsable",
            f"Cannot parse tox file: {exc}",
            relative_to(tox_path, root),
        )
        return ""

    for section in ("testenv:docs", "testenv"):
        if parser.has_option(section, "basepython"):
            return normalise_python(parser.get(section, "basepython"))
    return ""


def _linkcheck_finding(number: int, location: str, policy: str, audit: Audit) -> None:
    """Record a finding for a linkcheck environment that sets ``-W``."""
    severity = SEVERITY_ERROR if policy == "fail" else SEVERITY_WARNING
    audit.add(
        severity,
        "linkcheck-strict",
        (
            f"Line {number} runs the linkcheck builder with '-W'. External sites "
            "increasingly block automated requests, so a broken-link failure often "
            "reflects a bot policy rather than a genuine fault. Drop '-W' from the "
            "linkcheck environment."
        ),
        location,
    )


def check_sphinx_flags(
    tox_path: Path,
    root: Path,
    require_strict: bool,
    linkcheck_policy: str,
    audit: Audit,
) -> None:
    """Inspect sphinx-build invocations for warning-handling flags.

    A documentation build benefits from ``-W``, which turns a genuine
    markup problem into a failure. A link check does not: external sites
    increasingly refuse automated requests, so ``-W`` on a linkcheck
    builder converts somebody else's bot policy into a failed review.
    """
    try:
        text = tox_path.read_text(encoding="utf-8")
    except OSError:
        return

    location = relative_to(tox_path, root)
    saw_sphinx = False

    for number, line in enumerate(text.splitlines(), start=1):
        if not SPHINX_BUILD.search(line):
            continue
        saw_sphinx = True
        strict = bool(DASH_W.search(line))
        linkcheck = bool(LINKCHECK_BUILDER.search(line))

        if linkcheck and strict and linkcheck_policy != "ignore":
            _linkcheck_finding(number, location, linkcheck_policy, audit)
        elif not linkcheck and not strict and require_strict:
            audit.add(
                SEVERITY_WARNING,
                "sphinx-not-strict",
                f"Line {number} runs sphinx-build without '-W'; the configured policy expects it.",
                location,
            )

    if not saw_sphinx:
        audit.add(
            SEVERITY_WARNING,
            "sphinx-absent",
            "No sphinx-build command found; the documentation build may not run.",
            location,
        )


def resolve_python(
    tox_python: str, rtd_python: str, fallback: str, audit: Audit
) -> str:
    """Choose the interpreter the documentation build should use.

    Prefers what tox pins, because that is the interpreter the
    verification job runs. Falls back to the Read the Docs declaration and
    then to the supplied default.
    """
    if tox_python and rtd_python and tox_python != rtd_python:
        audit.add(
            SEVERITY_WARNING,
            "python-mismatch",
            (
                f"tox pins Python {tox_python} while Read the Docs builds with {rtd_python}. "
                "The verification job and the published build are not testing the same "
                "interpreter; align the two."
            ),
        )
    if tox_python:
        return tox_python
    if rtd_python:
        return rtd_python
    return fallback


def check_python_eol(
    version: str, supported: list[str], behaviour: str, audit: Audit
) -> bool:
    """Report whether the documentation Python version has reached end of life.

    ``supported`` arrives from the organisation's Python version action, so
    this function never needs its own copy of the release calendar. An
    empty list means the caller could not determine the supported set, and
    the check reports nothing rather than guessing.
    """
    if not version or not supported or behaviour == "ignore":
        return False
    if version in supported:
        return False

    severity = SEVERITY_ERROR if behaviour == "fail" else SEVERITY_WARNING
    audit.add(
        severity,
        "python-eol",
        (
            f"The documentation build uses Python {version}, which is absent from the "
            f"currently supported set ({', '.join(supported)}). Move the build to a "
            "supported release."
        ),
    )
    return True
