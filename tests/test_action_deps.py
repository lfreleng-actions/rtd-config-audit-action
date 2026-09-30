# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the action's runtime dependency install.

Reads the install step from ``action.yaml`` rather than copying it, so
the suite fails if the step drifts back to installing whatever version
PyPI currently serves.
"""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_FILE = REPO_ROOT / "action.yaml"

#: Step id of the install that runs when the runner's Python lacks PyYAML.
DEPS_STEP_ID = "deps"

#: Tokens that end a command inside a shell line.
COMMAND_ENDS = {";", "&&", "||", "|", "&", "then", "do"}

#: A requirement pinned to one exact version, with at least one hash.
PINNED = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==[^\s;]+(\s+--hash=sha256:[0-9a-f]{64})+"
)


def deps_step() -> dict[str, object]:
    """Return the action step that provides PyYAML."""
    action = cast(
        "dict[str, object]", yaml.safe_load(ACTION_FILE.read_text(encoding="utf-8"))
    )
    runs = cast("dict[str, object]", action["runs"])
    for step in cast("list[dict[str, object]]", runs["steps"]):
        if step.get("id") == DEPS_STEP_ID:
            return step
    raise AssertionError(f"action.yaml has no step with id '{DEPS_STEP_ID}'")


def pip_install_args(script: str) -> list[list[str]]:
    """Return the arguments of every ``pip install`` in a shell script."""
    installs: list[list[str]] = []
    for line in script.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        for index in range(len(tokens) - 1):
            if tokens[index : index + 2] != ["pip", "install"]:
                continue
            args: list[str] = []
            for token in tokens[index + 2 :]:
                if token in COMMAND_ENDS:
                    break
                args.append(token)
            installs.append(args)
    return installs


def requirement_files(args: list[str]) -> tuple[list[str], list[str]]:
    """Split pip arguments into requirement files and bare package names."""
    files: list[str] = []
    packages: list[str] = []
    expect_file = False
    for arg in args:
        if expect_file:
            files.append(arg)
            expect_file = False
        elif arg in {"-r", "--requirement"}:
            expect_file = True
        elif not arg.startswith("-"):
            packages.append(arg)
    return files, packages


def requirement_lines(path: Path) -> list[str]:
    """Return the logical requirement lines of a file, without comments."""
    joined = path.read_text(encoding="utf-8").replace("\\\n", " ")
    lines = (re.sub(r"(^|\s)#.*", "", line).strip() for line in joined.splitlines())
    return [line for line in lines if line]


def deps_installs() -> list[list[str]]:
    """Return the arguments of every ``pip install`` in the deps step."""
    return pip_install_args(str(deps_step()["run"]))


class RuntimeInstall(unittest.TestCase):
    """The action installs PyYAML only from a hash-pinned file."""

    def test_step_installs_something(self) -> None:
        """Guard the other tests against a step they no longer parse."""
        self.assertTrue(deps_installs(), "the deps step runs no 'pip install'")

    def test_every_install_requires_hashes(self) -> None:
        for args in deps_installs():
            self.assertIn("--require-hashes", args, f"unpinned install: {args}")

    def test_no_install_names_a_package_directly(self) -> None:
        """A bare package name resolves to whatever PyPI serves today."""
        for args in deps_installs():
            files, packages = requirement_files(args)
            self.assertEqual(packages, [], f"bare package in: {args}")
            self.assertTrue(files, f"no requirements file in: {args}")

    def test_requirements_resolve_through_the_action_path(self) -> None:
        """The file ships with the action, not with the caller's checkout."""
        env = cast("dict[str, object]", deps_step().get("env") or {})
        self.assertEqual(env.get("ACTION_PATH"), "${{ github.action_path }}")
        for args in deps_installs():
            for name in requirement_files(args)[0]:
                self.assertRegex(name, r"^\$\{?ACTION_PATH\}?/")
                local = re.sub(r"^\$\{?ACTION_PATH\}?/", "", name)
                self.assertTrue((REPO_ROOT / local).is_file(), f"missing: {local}")

    def test_requirements_are_hash_pinned(self) -> None:
        names: set[str] = set()
        for args in deps_installs():
            for name in requirement_files(args)[0]:
                path = REPO_ROOT / re.sub(r"^\$\{?ACTION_PATH\}?/", "", name)
                lines = requirement_lines(path)
                self.assertTrue(lines, f"{path.name} lists no requirements")
                for line in lines:
                    match = PINNED.fullmatch(line)
                    self.assertIsNotNone(match, f"not hash-pinned: {line[:60]}")
                    if match:
                        names.add(match["name"].lower())
        self.assertIn("pyyaml", names)


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
