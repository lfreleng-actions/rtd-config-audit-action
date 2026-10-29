# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the runner parity report.

A documentation build runs twice: once on a GitHub runner through tox
and once on Read the Docs. These tests cover what the audit reports
about the differences between the two environments.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

import yaml

#: A decoded audit report.
Report = dict[str, object]

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: Finding codes the parity report adds.
PARITY_CODES = {
    "build-os-floating",
    "build-os-unsupported",
    "runner-os-mismatch",
    "apt-package-invalid",
    "parity-apt-packages",
    "parity-build-jobs",
    "parity-build-commands",
}

#: Outputs the parity report adds.
PARITY_OUTPUTS = ("runner", "runner_os", "apt_packages", "build_jobs", "build_commands")

#: A path that never exists, so the audit sees no runner OS.
NO_OS_RELEASE = str(FIXTURES / "absent-os-release")


def audit_path(project: Path, *extra: str) -> tuple[int, Report]:
    """Run the audit against a directory with no runner OS by default.

    A later ``--os-release`` in ``extra`` overrides the default, so a
    test chooses the runner OS explicitly rather than inheriting the
    host's.
    """
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "lib.rtd_audit",
            "--path-prefix",
            str(project),
            "--os-release",
            NO_OS_RELEASE,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
    )
    return completed.returncode, cast("Report", json.loads(completed.stdout))


def run_audit(fixture: str, *extra: str) -> tuple[int, Report]:
    """Run the audit against a named fixture project."""
    return audit_path(FIXTURES / fixture, *extra)


def findings_of(report: Report) -> list[dict[str, object]]:
    """Return the findings list from a decoded report."""
    return cast("list[dict[str, object]]", report["findings"])


def codes(report: Report) -> set[str]:
    """Collect the finding codes in a report."""
    return {str(f["code"]) for f in findings_of(report)}


def os_release(directory: str, distribution: str, version: str) -> str:
    """Write an ``os-release`` file and return its path."""
    path = Path(directory) / "os-release"
    _ = path.write_text(
        f'NAME="Test"\nID={distribution}\nVERSION_ID="{version}"\n',
        encoding="utf-8",
    )
    return str(path)


def write_config(directory: str, build: str) -> Path:
    """Write a configuration whose ``build`` block holds the given lines."""
    project = Path(directory) / "project"
    project.mkdir()
    _ = (project / ".readthedocs.yaml").write_text(
        f'version: 2\nbuild:\n  tools:\n    python: "3.13"\n{build}',
        encoding="utf-8",
    )
    return project


def severity_of(report: Report, code: str) -> str:
    """Return the severity of the first finding with a code."""
    for finding in findings_of(report):
        if finding["code"] == code:
            return str(finding["severity"])
    raise AssertionError(f"no finding with code {code}")


def message_of(report: Report, code: str) -> str:
    """Return the message of the first finding with a code."""
    for finding in findings_of(report):
        if finding["code"] == code:
            return str(finding["message"])
    raise AssertionError(f"no finding with code {code}")


class RunnerLabel(unittest.TestCase):
    """``build.os`` maps onto the GitHub-hosted runner of the same name."""

    def test_maps_each_pinned_release(self) -> None:
        for release in ("ubuntu-22.04", "ubuntu-24.04", "ubuntu-26.04"):
            with tempfile.TemporaryDirectory() as tmp:
                project = write_config(tmp, f"  os: {release}\n")
                _, report = audit_path(project)
            self.assertEqual(report["runner"], release)
            self.assertNotIn("build-os-floating", codes(report))
            self.assertNotIn("build-os-unsupported", codes(report))

    def test_floating_alias_warns(self) -> None:
        code, report = run_audit("lts-latest")
        self.assertEqual(code, 0)
        self.assertEqual(report["runner"], "")
        self.assertEqual(severity_of(report, "build-os-floating"), "warning")

    def test_unsupported_release_fails(self) -> None:
        code, report = run_audit("unsupported-os")
        self.assertEqual(code, 1)
        self.assertEqual(report["runner"], "")
        self.assertEqual(severity_of(report, "build-os-unsupported"), "error")

    def test_empty_release_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = write_config(tmp, '  os: ""\n')
            code, report = audit_path(project)
        self.assertEqual(code, 1)
        self.assertEqual(report["runner"], "")
        self.assertEqual(severity_of(report, "build-os-unsupported"), "error")
        self.assertNotIn("build-os-missing", codes(report))

    def test_missing_os_keeps_the_existing_error(self) -> None:
        _, report = run_audit("legacy")
        self.assertEqual(report["runner"], "")
        self.assertIn("build-os-missing", codes(report))
        self.assertNotIn("build-os-unsupported", codes(report))


class RunnerOs(unittest.TestCase):
    """The audit compares its own runner OS with ``build.os``."""

    def test_matching_release_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os_release(tmp, "ubuntu", "24.04")
            _, report = run_audit("good", "--os-release", path)
        self.assertEqual(report["runner_os"], "ubuntu-24.04")
        self.assertNotIn("runner-os-mismatch", codes(report))

    def test_divergent_release_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os_release(tmp, "ubuntu", "26.04")
            code, report = run_audit("good", "--os-release", path)
        self.assertEqual(code, 0)
        self.assertEqual(report["runner_os"], "ubuntu-26.04")
        self.assertEqual(severity_of(report, "runner-os-mismatch"), "warning")
        message = message_of(report, "runner-os-mismatch")
        self.assertIn("ubuntu-26.04", message)
        self.assertIn("ubuntu-24.04", message)

    def test_other_distribution_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os_release(tmp, "debian", "12")
            _, report = run_audit("good", "--os-release", path)
        self.assertEqual(report["runner_os"], "")
        self.assertNotIn("runner-os-mismatch", codes(report))

    def test_absent_file_reports_nothing(self) -> None:
        _, report = run_audit("good")
        self.assertEqual(report["runner_os"], "")
        self.assertNotIn("runner-os-mismatch", codes(report))


class ExtraSteps(unittest.TestCase):
    """Steps Read the Docs runs that a tox build never does."""

    def test_reports_the_cps_configuration(self) -> None:
        code, report = run_audit("parity-cps")
        self.assertEqual(code, 0)
        self.assertEqual(report["runner"], "ubuntu-24.04")
        self.assertEqual(report["apt_packages"], "openjdk-21-jdk-headless")
        self.assertEqual(report["build_jobs"], "pre_build")
        self.assertFalse(report["build_commands"])
        self.assertEqual(severity_of(report, "parity-apt-packages"), "notice")
        self.assertIn(
            "openjdk-21-jdk-headless", message_of(report, "parity-apt-packages")
        )
        self.assertEqual(severity_of(report, "parity-build-jobs"), "warning")
        jobs = message_of(report, "parity-build-jobs")
        self.assertIn("pre_build", jobs)
        self.assertIn("bash docs/generate-openapi.sh", jobs)

    def test_follows_declared_order_and_drops_invalid_names(self) -> None:
        build = (
            "  os: ubuntu-24.04\n"
            "  apt_packages:\n"
            "    - zlib1g-dev\n"
            "    - graphviz\n"
            "    - 'graphviz; rm -rf /'\n"
            "    - libxml2-dev\n"
            "  jobs:\n"
            "    post_build:\n"
            "      - echo after\n"
            "    pre_build:\n"
            "      - echo before\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = write_config(tmp, build)
            code, report = audit_path(project)
        self.assertEqual(code, 1)
        self.assertEqual(report["apt_packages"], "zlib1g-dev graphviz libxml2-dev")
        self.assertEqual(report["build_jobs"], "post_build pre_build")
        self.assertEqual(severity_of(report, "apt-package-invalid"), "error")
        self.assertIn("graphviz; rm -rf /", message_of(report, "apt-package-invalid"))

    def test_reports_commands_for_each_output_format(self) -> None:
        build = (
            "  os: ubuntu-24.04\n"
            "  jobs:\n"
            "    build:\n"
            "      html:\n"
            "        - python -m sphinx -b html docs out/html\n"
            "      pdf:\n"
            "        - make latexpdf\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = write_config(tmp, build)
            _, report = audit_path(project)
        self.assertEqual(report["build_jobs"], "build")
        jobs = message_of(report, "parity-build-jobs")
        self.assertIn("build.html (python -m sphinx -b html docs out/html)", jobs)
        self.assertIn("build.pdf (make latexpdf)", jobs)

    def test_survives_a_self_referencing_job_mapping(self) -> None:
        build = "  os: ubuntu-24.04\n  jobs:\n    build: &job\n      html: *job\n"
        with tempfile.TemporaryDirectory() as tmp:
            project = write_config(tmp, build)
            _, report = audit_path(project)
        self.assertEqual(report["build_jobs"], "build")
        self.assertIn("build.html", message_of(report, "parity-build-jobs"))

    def test_reports_build_commands(self) -> None:
        code, report = run_audit("build-commands")
        self.assertEqual(code, 0)
        self.assertTrue(report["build_commands"])
        self.assertEqual(severity_of(report, "parity-build-commands"), "warning")

    def test_reports_a_null_build_commands_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = write_config(tmp, "  os: ubuntu-24.04\n  commands:\n")
            _, report = audit_path(project)
        self.assertTrue(report["build_commands"])
        self.assertEqual(severity_of(report, "parity-build-commands"), "warning")


class GoodFixtureParity(unittest.TestCase):
    """A compliant project gains nothing beyond the runner comparison."""

    def test_reports_a_runner_and_no_new_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os_release(tmp, "ubuntu", "24.04")
            code, report = run_audit("good", "--os-release", path)
        self.assertEqual(code, 0)
        self.assertEqual(report["runner"], "ubuntu-24.04")
        self.assertEqual(report["apt_packages"], "")
        self.assertEqual(report["build_jobs"], "")
        self.assertFalse(report["build_commands"])
        self.assertEqual(report["warnings"], 0)
        self.assertFalse(codes(report) & PARITY_CODES)

    def test_a_different_runner_adds_only_the_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os_release(tmp, "ubuntu", "26.04")
            _, report = run_audit("good", "--os-release", path)
        self.assertEqual(codes(report) & PARITY_CODES, {"runner-os-mismatch"})


class OutputWiring(unittest.TestCase):
    """Every parity value reaches the action's outputs."""

    def test_emit_outputs_publishes_each_value(self) -> None:
        sys.path.insert(0, str(REPO_ROOT))
        from lib.emit_outputs import SCALAR_KEYS  # noqa: PLC0415

        for key in PARITY_OUTPUTS:
            self.assertIn(key, SCALAR_KEYS)

    def test_action_declares_each_output(self) -> None:
        action = cast(
            "dict[str, object]",
            yaml.safe_load((REPO_ROOT / "action.yaml").read_text(encoding="utf-8")),
        )
        outputs = cast("dict[str, object]", action["outputs"])
        for key in PARITY_OUTPUTS:
            self.assertIn(key, outputs)


def parity_report(**overrides: object) -> dict[str, object]:
    """Build a minimal report for the summary renderer."""
    report: dict[str, object] = {
        "config_found": True,
        "config_path": ".readthedocs.yaml",
        "passed": True,
        "build_os": "ubuntu-24.04",
        "runner_os": "ubuntu-24.04",
        "rtd_python": "3.13",
        "docs_python": "3.13",
        "apt_packages": "",
        "build_jobs": "",
        "findings": [],
    }
    report.update(overrides)
    return report


def summary_of(report: dict[str, object]) -> str:
    """Render a report summary."""
    sys.path.insert(0, str(REPO_ROOT))
    from lib.render_summary import render  # noqa: PLC0415

    return render(report)


class SummaryParity(unittest.TestCase):
    """The step summary sets the two builds side by side."""

    def test_section_follows_the_property_table(self) -> None:
        text = summary_of(parity_report())
        self.assertIn("Runner parity", text)
        self.assertLess(text.index("| Warnings |"), text.index("Runner parity"))
        self.assertIn("| Aspect | Read the Docs | This runner | Match |", text)
        self.assertIn("| OS | `ubuntu-24.04` | `ubuntu-24.04` | ✅ |", text)
        self.assertIn("| Python | `3.13` | `3.13` | ✅ |", text)

    def test_omits_empty_rows(self) -> None:
        text = summary_of(parity_report())
        self.assertIn("| OS |", text)
        self.assertNotIn("APT packages", text)
        self.assertNotIn("Build jobs", text)

    def test_flags_divergence(self) -> None:
        text = summary_of(
            parity_report(
                runner_os="ubuntu-26.04",
                docs_python="3.12",
                apt_packages="openjdk-21-jdk-headless graphviz",
                build_jobs="pre_build",
            )
        )
        self.assertIn("| OS | `ubuntu-24.04` | `ubuntu-26.04` | ⚠️ |", text)
        self.assertIn("| Python | `3.13` | `3.12` | ⚠️ |", text)
        self.assertIn(
            "| APT packages | `openjdk-21-jdk-headless` `graphviz` "
            + "| not installed by tox | ⚠️ |",
            text,
        )
        self.assertIn("| Build jobs | `pre_build` | not run by tox | ⚠️ |", text)

    def test_absent_without_a_configuration(self) -> None:
        text = summary_of({"config_found": False})
        self.assertNotIn("Runner parity", text)


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
