# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the interpreter the action runs its helpers on.

The action must not run whichever ``python3`` the caller's PATH finds
first. These tests read the steps from ``action.yaml`` and run the
helper launcher the way the action does, from a workspace that plants
modules of the same names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_FILE = REPO_ROOT / "action.yaml"
LAUNCHER = REPO_ROOT / "lib" / "launch.py"

#: Step id of the setup-python step that chooses the interpreter.
PYTHON_STEP_ID = "python"

#: The value every step that runs Python binds to ``PYTHON``.
PYTHON_PATH = "${{ steps.python.outputs.python-path }}"

#: A commit-pinned setup-python reference.
SETUP_PYTHON = re.compile(r"^actions/setup-python@[0-9a-f]{40}$")

#: A bare interpreter name used as a command, which PATH resolves.
BARE_PYTHON = re.compile(r"(?:^|[\s;|&(`])python(?:3(?:\.\d+)?)?(?=\s)")

#: A run of the trusted interpreter, capturing the first argument.
TRUSTED_RUN = re.compile(r'"\$\{?PYTHON\}?"\s+(\S+)')


def action_steps() -> list[dict[str, object]]:
    """Return the composite action's steps."""
    action = cast(
        "dict[str, object]", yaml.safe_load(ACTION_FILE.read_text(encoding="utf-8"))
    )
    runs = cast("dict[str, object]", action["runs"])
    return cast("list[dict[str, object]]", runs["steps"])


def script_lines(step: dict[str, object]) -> list[str]:
    """Return a run step's shell lines, without comments."""
    joined = str(step.get("run", "")).replace("\\\n", " ")
    lines = (line.strip() for line in joined.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def python_steps() -> list[dict[str, object]]:
    """Return the run steps that start the trusted interpreter."""
    return [
        step
        for step in action_steps()
        if any(TRUSTED_RUN.search(line) for line in script_lines(step))
    ]


class ActionInterpreter(unittest.TestCase):
    """The action chooses its interpreter rather than the caller's PATH."""

    def test_setup_python_is_pinned_and_leaves_path_alone(self) -> None:
        steps = [s for s in action_steps() if s.get("id") == PYTHON_STEP_ID]
        self.assertEqual(len(steps), 1, "no setup-python step with id 'python'")
        step = steps[0]
        self.assertRegex(str(step.get("uses", "")), SETUP_PYTHON)
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)
        inputs = cast("dict[str, object]", step.get("with") or {})
        self.assertIn(str(inputs.get("update-environment")).lower(), {"false"})

    def test_setup_python_runs_before_any_python(self) -> None:
        ids = [step.get("id") for step in action_steps()]
        first_python = min(ids.index(step.get("id")) for step in python_steps())
        self.assertLess(ids.index(PYTHON_STEP_ID), first_python)

    def test_no_step_runs_python_from_path(self) -> None:
        for step in action_steps():
            for line in script_lines(step):
                self.assertIsNone(BARE_PYTHON.search(line), f"{step.get('id')}: {line}")

    def test_python_steps_bind_the_setup_python_output(self) -> None:
        self.assertTrue(python_steps(), "no step runs the trusted interpreter")
        for step in python_steps():
            env = cast("dict[str, object]", step.get("env") or {})
            self.assertEqual(env.get("PYTHON"), PYTHON_PATH, step.get("id"))

    def test_every_python_run_is_isolated(self) -> None:
        for step in python_steps():
            for line in script_lines(step):
                for match in TRUSTED_RUN.finditer(line):
                    self.assertEqual(match[1], "-I", f"{step.get('id')}: {line}")


def plant_modules(root: Path) -> Path:
    """Plant modules that shadow the action's in ``root``; return the marker.

    Each planted module records its name in the marker file and exits
    non-zero, so importing one in place of the real module shows up.
    """
    marker = root / "planted-ran"
    body = f"open({str(marker)!r}, 'a').write(__name__ + '\\n')\nraise SystemExit(97)\n"
    (root / "lib").mkdir()
    _ = (root / "lib" / "__init__.py").write_text(body, encoding="utf-8")
    for module in ("json", "argparse", "sitecustomize"):
        _ = (root / f"{module}.py").write_text(body, encoding="utf-8")
    return marker


def launch(
    workspace: Path, *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run the launcher from ``workspace``, as the action does."""
    env = dict(os.environ, PYTHONPATH=str(workspace))
    return subprocess.run(
        [sys.executable, "-I", str(LAUNCHER), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=workspace,
        env=env,
        check=False,
    )


class Launcher(unittest.TestCase):
    """The launcher runs the action's own helpers under ``python -I``."""

    def test_runs_the_named_helper(self) -> None:
        report = {"findings": [{"severity": "error", "code": "x", "message": "m"}]}
        with tempfile.TemporaryDirectory() as tmp:
            marker = plant_modules(Path(tmp))
            result = launch(
                Path(tmp), "lib.render_annotations", stdin=json.dumps(report)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "::error title=RTD audit%3A x::m\n")
            self.assertFalse(marker.exists(), "imported a planted module")

    def test_passes_arguments_and_exit_status(self) -> None:
        project = REPO_ROOT / "tests" / "fixtures" / "good"
        with tempfile.TemporaryDirectory() as tmp:
            marker = plant_modules(Path(tmp))
            result = launch(
                Path(tmp),
                "lib.rtd_audit",
                "--path-prefix",
                str(project),
                "--supported-versions",
                "",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = cast("dict[str, object]", json.loads(result.stdout))
            self.assertIs(report["passed"], True)
            self.assertFalse(marker.exists(), "imported a planted module")

    def test_refuses_a_module_outside_the_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = plant_modules(Path(tmp))
            result = launch(Path(tmp), "json")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists(), "imported a planted module")


def stub_wheel(directory: Path) -> Path:
    """Build a wheel providing a stand-in ``yaml`` module; return its path."""
    wheel = directory / "yaml_stub-1.0-py3-none-any.whl"
    info = "yaml_stub-1.0.dist-info"
    files = {
        "yaml/__init__.py": "__version__ = 'stub'\n",
        f"{info}/METADATA": "Metadata-Version: 2.1\nName: yaml-stub\nVersion: 1.0\n",
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = "".join(f"{name},,\n" for name in files) + f"{info}/RECORD,,\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
        archive.writestr(f"{info}/RECORD", record)
    return wheel


def deps_script() -> str:
    """Return the shell script of the step that provides PyYAML."""
    for step in action_steps():
        if step.get("id") == "deps":
            return str(step["run"])
    raise AssertionError("action.yaml has no step with id 'deps'")


class DependencyInstall(unittest.TestCase):
    """The caller's pip settings cannot move PyYAML off the import path.

    Runs the deps step against a fresh virtual environment lacking
    PyYAML, with a requirements file that installs a local stub wheel,
    so no network is needed.
    """

    def run_deps(self, extra_env: dict[str, str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / "venv"
            _ = subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                check=True,
                capture_output=True,
            )
            python = venv / "bin" / "python"
            wheel = stub_wheel(root)
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            action = root / "action"
            action.mkdir()
            _ = (action / "requirements.txt").write_text(
                f"{wheel} --hash=sha256:{digest}\n", encoding="utf-8"
            )
            home = root / "home"
            home.mkdir()
            env = dict(
                os.environ,
                ACTION_PATH=str(action),
                PYTHON=str(python),
                HOME=str(home),
                PYTHONUSERBASE=str(home / ".local"),
                **extra_env,
            )
            _ = env.pop("VIRTUAL_ENV", None)
            result = subprocess.run(
                ["bash", "-c", deps_script()],
                capture_output=True,
                text=True,
                cwd=root,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            imported = subprocess.run(
                [str(python), "-I", "-c", "import yaml; print(yaml.__version__)"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(imported.stdout.strip(), "stub", imported.stderr)

    def test_pip_user_cannot_redirect_the_install(self) -> None:
        self.run_deps({"PIP_USER": "true"})

    def test_pip_config_file_cannot_redirect_the_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "pip.conf"
            _ = config.write_text(
                f"[install]\ntarget = {Path(tmp) / 'elsewhere'}\n", encoding="utf-8"
            )
            self.run_deps({"PIP_CONFIG_FILE": str(config)})


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
