# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Compare the Read the Docs build environment with the GitHub runner.

A documentation build runs twice: on a GitHub runner through tox, and on
Read the Docs. When the two environments differ, one can fail while the
other passes. These checks report the runner label matching
``build.os``, the OS this audit runs on, and the steps Read the Docs
runs that a tox build never does, so a caller can close the gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from lib.rtd_model import (
    APT_PACKAGE_NAME,
    FLOATING_BUILD_OS,
    PINNED_BUILD_OS,
    SEVERITY_ERROR,
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    Audit,
    nested_get,
)

#: An Ubuntu ``VERSION_ID`` such as ``24.04``.
UBUNTU_VERSION = re.compile(r"\d+\.\d+")


@dataclass
class Parity:
    """What the audit learns about the two build environments."""

    runner: str = ""
    apt_packages: list[str] = field(default_factory=list)
    build_jobs: list[str] = field(default_factory=list)
    build_commands: bool = False


def runner_for(build_os: str, location: str, audit: Audit) -> str:
    """Return the GitHub-hosted runner label matching ``build.os``.

    The caller skips this when ``build.os`` is absent, having already
    recorded ``build-os-missing``; an empty declared value is rejected.
    """
    if build_os in PINNED_BUILD_OS:
        return build_os
    if build_os == FLOATING_BUILD_OS:
        audit.add(
            SEVERITY_WARNING,
            "build-os-floating",
            (
                f"'build.os' is {FLOATING_BUILD_OS!r}, which Read the Docs moves on its "
                "own schedule and which may not match the latest Ubuntu LTS. No runner "
                "can match it reliably; pin 'build.os' to a release such as "
                f"{PINNED_BUILD_OS[-1]!r}."
            ),
            location,
        )
        return ""
    audit.add(
        SEVERITY_ERROR,
        "build-os-unsupported",
        (
            f"'build.os' is {build_os!r}, which Read the Docs does not accept; use one "
            f"of {', '.join(PINNED_BUILD_OS)}."
        ),
        location,
    )
    return ""


def read_runner_os(path: Path) -> str:
    """Return the runner OS as ``ubuntu-<VERSION_ID>``, or empty.

    Reads an ``os-release`` file and answers only for Ubuntu, the one
    distribution that has a matching Read the Docs ``build.os``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")

    version = values.get("VERSION_ID", "")
    if values.get("ID") != "ubuntu" or not UBUNTU_VERSION.fullmatch(version):
        return ""
    return f"ubuntu-{version}"


def check_runner_os(runner: str, runner_os: str, location: str, audit: Audit) -> None:
    """Warn when this job runs on a different OS from Read the Docs."""
    if runner and runner_os and runner != runner_os:
        audit.add(
            SEVERITY_WARNING,
            "runner-os-mismatch",
            (
                f"This job ran on {runner_os}; Read the Docs builds on {runner}. "
                "One build can fail while the other passes; run the documentation "
                f"job on {runner}."
            ),
            location,
        )


def read_apt_packages(
    config: dict[str, object], location: str, audit: Audit
) -> list[str]:
    """Return the valid ``build.apt_packages`` entries in declared order.

    A caller passes these names to ``apt-get``, so an entry that breaks
    the Debian naming rules is dropped and reported rather than passed on.
    """
    declared = nested_get(config, "build.apt_packages")
    if declared is None:
        return []
    if not isinstance(declared, list):
        audit.add(
            SEVERITY_ERROR,
            "apt-package-invalid",
            "'build.apt_packages' must be a list of package names",
            location,
        )
        return []

    packages: list[str] = []
    for entry in cast("list[object]", declared):
        if isinstance(entry, str) and APT_PACKAGE_NAME.fullmatch(entry):
            packages.append(entry)
            continue
        audit.add(
            SEVERITY_ERROR,
            "apt-package-invalid",
            f"'build.apt_packages' holds {entry!r}, which is not a valid package name",
            location,
        )

    if packages:
        audit.add(
            SEVERITY_NOTICE,
            "parity-apt-packages",
            (
                f"Read the Docs installs APT packages {' '.join(packages)}; a GitHub "
                "build must install them too to match."
            ),
            location,
        )
    return packages


def describe_commands(name: str, commands: object) -> str:
    """Render a step name with its command list, if it has one."""
    if not isinstance(commands, list):
        return name
    steps = "; ".join(str(c) for c in cast("list[object]", commands))
    return f"{name} ({steps})" if steps else name


def describe_job(name: str, commands: object) -> str:
    """Render one ``build.jobs`` step and its commands.

    ``build.jobs.build`` maps output formats to their own command lists,
    so a mapping is rendered as one ``step.format`` entry per format.
    Only that one level is expanded: a YAML alias can make the mapping
    refer to itself, and following it would never end.
    """
    if not isinstance(commands, dict):
        return describe_commands(name, commands)
    formats = cast("dict[object, object]", commands)
    if not formats:
        return name
    return ", ".join(
        describe_commands(f"{name}.{fmt}", steps) for fmt, steps in formats.items()
    )


def read_build_jobs(
    config: dict[str, object], location: str, audit: Audit
) -> list[str]:
    """Return the ``build.jobs`` step names in declared order."""
    declared = nested_get(config, "build.jobs")
    if not isinstance(declared, dict) or not declared:
        return []

    jobs = cast("dict[object, object]", declared)
    names = [str(name) for name in jobs]
    described = ", ".join(
        describe_job(str(name), steps) for name, steps in jobs.items()
    )
    audit.add(
        SEVERITY_WARNING,
        "parity-build-jobs",
        f"Read the Docs runs build.jobs steps a tox build does not: {described}",
        location,
    )
    return names


def check_build_commands(
    config: dict[str, object], location: str, audit: Audit
) -> bool:
    """Report whether ``build.commands`` replaces the Read the Docs build.

    Presence of the key decides, so an explicitly null ``commands:`` is
    still reported rather than mistaken for an omitted one.
    """
    build = config.get("build")
    if not isinstance(build, dict) or "commands" not in build:
        return False
    audit.add(
        SEVERITY_WARNING,
        "parity-build-commands",
        (
            "'build.commands' replaces the whole Read the Docs build with its own "
            "commands, which a tox build cannot reproduce."
        ),
        location,
    )
    return True


def check_parity(
    config: dict[str, object],
    location: str,
    build_os: str,
    runner_os: str,
    audit: Audit,
) -> Parity:
    """Run every parity check against a parsed configuration."""
    runner = ""
    if nested_get(config, "build.os") is not None:
        runner = runner_for(build_os, location, audit)
    check_runner_os(runner, runner_os, location, audit)
    return Parity(
        runner=runner,
        apt_packages=read_apt_packages(config, location, audit),
        build_jobs=read_build_jobs(config, location, audit),
        build_commands=check_build_commands(config, location, audit),
    )
