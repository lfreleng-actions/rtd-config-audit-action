# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the ReadTheDocs configuration audit.

Runs the audit against the fixture projects under ``tests/fixtures`` and
checks both the findings and the resolved values. Uses only the standard
library plus PyYAML so the suite runs anywhere the action itself runs.
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

#: A decoded audit report.
Report = dict[str, object]

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: A stable supported set keeps the suite independent of the release
#: calendar; the action supplies the live set at runtime.
SUPPORTED = "3.10 3.11 3.12 3.13 3.14"


def audit_path(project: Path, *extra: str) -> tuple[int, Report]:
    """Run the audit against a directory and return its code and report."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "lib.rtd_audit",
            "--path-prefix",
            str(project),
            "--supported-versions",
            SUPPORTED,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
    )
    report = cast("Report", json.loads(completed.stdout))
    return completed.returncode, report


def run_audit(fixture: str, *extra: str) -> tuple[int, Report]:
    """Run the audit against a named fixture project."""
    return audit_path(FIXTURES / fixture, *extra)


def findings_of(report: Report) -> list[dict[str, object]]:
    """Return the findings list from a decoded report."""
    return cast("list[dict[str, object]]", report["findings"])


def codes(report: Report) -> set[str]:
    """Collect the finding codes in a report."""
    return {str(f["code"]) for f in findings_of(report)}


class GoodProject(unittest.TestCase):
    """A project matching current practice passes cleanly."""

    def test_passes_without_findings(self) -> None:
        code, report = run_audit("good")
        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["warnings"], 0)

    def test_resolves_values(self) -> None:
        _, report = run_audit("good")
        self.assertTrue(report["config_found"])
        self.assertEqual(report["config_path"], ".readthedocs.yaml")
        self.assertEqual(report["tox_path"], "docs/tox.ini")
        self.assertEqual(report["build_os"], "ubuntu-24.04")
        self.assertEqual(report["rtd_python"], "3.13")
        self.assertEqual(report["tox_python"], "3.13")
        self.assertEqual(report["docs_python"], "3.13")
        self.assertFalse(report["python_mismatch"])
        self.assertFalse(report["python_eol"])


class LegacyProject(unittest.TestCase):
    """A configuration using retired keys fails with actionable findings."""

    def test_fails(self) -> None:
        code, report = run_audit("legacy")
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])

    def test_reports_retired_and_missing_keys(self) -> None:
        _, report = run_audit("legacy")
        found = codes(report)
        self.assertIn("retired-key", found)
        self.assertIn("build-os-missing", found)
        self.assertIn("python-missing", found)

    def test_detects_end_of_life_python(self) -> None:
        _, report = run_audit("legacy")
        self.assertTrue(report["python_eol"])
        self.assertIn("python-eol", codes(report))

    def test_eol_can_escalate_to_error(self) -> None:
        _, report = run_audit("legacy", "--eol-behaviour", "fail")
        severities = {str(f["code"]): str(f["severity"]) for f in findings_of(report)}
        self.assertEqual(severities["python-eol"], "error")

    def test_eol_can_be_ignored(self) -> None:
        _, report = run_audit("legacy", "--eol-behaviour", "ignore")
        self.assertNotIn("python-eol", codes(report))


class PythonMismatch(unittest.TestCase):
    """Divergent tox and ReadTheDocs versions surface as a warning."""

    def test_warns_without_failing(self) -> None:
        code, report = run_audit("mismatch")
        self.assertEqual(code, 0)
        self.assertTrue(report["python_mismatch"])
        self.assertIn("python-mismatch", codes(report))

    def test_prefers_the_tox_interpreter(self) -> None:
        _, report = run_audit("mismatch")
        self.assertEqual(report["tox_python"], "3.13")
        self.assertEqual(report["rtd_python"], "3.11")
        self.assertEqual(report["docs_python"], "3.13")

    def test_fail_on_warning_promotes_the_warning(self) -> None:
        code, _ = run_audit("mismatch", "--fail-on-warning")
        self.assertEqual(code, 1)


class LinkcheckPolicy(unittest.TestCase):
    """A strict linkcheck environment draws a finding by default.

    External sites increasingly refuse automated requests, so treating a
    broken link as a build failure reports somebody else's bot policy.
    """

    def test_warns_by_default(self) -> None:
        code, report = run_audit("strict-linkcheck")
        self.assertEqual(code, 0)
        self.assertIn("linkcheck-strict", codes(report))

    def test_can_escalate(self) -> None:
        code, report = run_audit("strict-linkcheck", "--linkcheck-policy", "fail")
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])

    def test_can_be_ignored(self) -> None:
        _, report = run_audit("strict-linkcheck", "--linkcheck-policy", "ignore")
        self.assertNotIn("linkcheck-strict", codes(report))

    def test_html_build_with_dash_w_stays_clean(self) -> None:
        """The good fixture uses -W on html but not on linkcheck."""
        _, report = run_audit("good")
        self.assertNotIn("linkcheck-strict", codes(report))


class BrokenConfig(unittest.TestCase):
    """Unparsable YAML fails rather than falling back to guesswork.

    The invalid document gets written at run time; committing it would
    fail the repository's own YAML linting.
    """

    def test_reports_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text(
                "version: 2\nbuild:\n  os: [unclosed\n",
                encoding="utf-8",
            )
            code, report = audit_path(project)
        self.assertEqual(code, 1)
        self.assertIn("config-unparsable", codes(report))

    def test_reports_non_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text(
                "- one\n- two\n", encoding="utf-8"
            )
            code, report = audit_path(project)
        self.assertEqual(code, 1)
        self.assertIn("config-not-mapping", codes(report))

    def test_reports_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text("", encoding="utf-8")
            code, report = audit_path(project)
        self.assertEqual(code, 1)
        self.assertIn("config-empty", codes(report))


class MissingConfig(unittest.TestCase):
    """A project with no configuration reports the absence."""

    def test_reports_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, report = audit_path(Path(tmp))
        self.assertEqual(code, 1)
        self.assertFalse(report["config_found"])
        self.assertIn("config-missing", codes(report))

    def test_accepts_the_yml_spelling(self) -> None:
        """ReadTheDocs accepts both spellings, so the audit must too."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yml").write_text(
                'version: 2\nbuild:\n  os: ubuntu-24.04\n  tools:\n    python: "3.13"\n',
                encoding="utf-8",
            )
            _, report = audit_path(project)
        self.assertTrue(report["config_found"])
        self.assertEqual(report["config_path"], ".readthedocs.yml")


class FilePolicy(unittest.TestCase):
    """Required and forbidden file lists arrive as inputs, not code."""

    def test_missing_required_file_fails(self) -> None:
        code, report = run_audit("good", "--required-files", "docs/index.rst")
        self.assertEqual(code, 1)
        self.assertIn("required-file-missing", codes(report))

    def test_present_required_file_passes(self) -> None:
        code, report = run_audit("good", "--required-files", "docs/tox.ini")
        self.assertEqual(code, 0)
        self.assertNotIn("required-file-missing", codes(report))

    def test_forbidden_file_fails(self) -> None:
        code, report = run_audit("good", "--forbidden-files", "docs/tox.ini")
        self.assertEqual(code, 1)
        self.assertIn("forbidden-file-present", codes(report))

    def test_accepts_comma_separated_lists(self) -> None:
        _, report = run_audit("good", "--required-files", "a.txt,b.txt")
        missing = [
            f for f in findings_of(report) if f["code"] == "required-file-missing"
        ]
        self.assertEqual(len(missing), 2)

    def test_empty_policy_checks_nothing(self) -> None:
        _, report = run_audit("good", "--required-files", "")
        self.assertNotIn("required-file-missing", codes(report))


class BuildOsPolicy(unittest.TestCase):
    """The expected build image arrives as an input."""

    def test_matching_value_passes(self) -> None:
        _, report = run_audit("good", "--build-os", "ubuntu-24.04")
        self.assertNotIn("build-os-mismatch", codes(report))

    def test_divergent_value_warns(self) -> None:
        _, report = run_audit("good", "--build-os", "ubuntu-22.04")
        self.assertIn("build-os-mismatch", codes(report))

    def test_unset_policy_accepts_any_value(self) -> None:
        _, report = run_audit("mismatch")
        self.assertNotIn("build-os-mismatch", codes(report))


class PathReporting(unittest.TestCase):
    """Findings quote paths relative to the project root."""

    def test_paths_stay_relative(self) -> None:
        _, report = run_audit("strict-linkcheck")
        paths = {str(f["path"]) for f in findings_of(report) if f.get("path")}
        self.assertIn("docs/tox.ini", paths)
        for path in paths:
            self.assertFalse(Path(path).is_absolute(), f"{path} should be relative")

    def test_reports_a_missing_directory(self) -> None:
        """An invalid path_prefix reports rather than raising."""
        code, report = audit_path(Path("/nonexistent/project/path"))
        self.assertEqual(code, 1)
        self.assertIn("path-missing", codes(report))


class ToxDiscovery(unittest.TestCase):
    """The audit reports which tox file it inspected.

    A caller running the same documentation build reads this rather than
    repeating the search, so the two cannot disagree about which file
    holds the documentation environments.
    """

    def test_reports_a_docs_tox_file(self) -> None:
        _, report = run_audit("good")
        self.assertEqual(report["tox_path"], "docs/tox.ini")
        self.assertEqual(report["tox_dir"], "docs")

    def test_finds_a_root_tox_file(self) -> None:
        """A project may keep its documentation environments at the root."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text(
                'version: 2\nbuild:\n  os: ubuntu-24.04\n  tools:\n    python: "3.13"\n',
                encoding="utf-8",
            )
            _ = (project / "tox.ini").write_text(
                "[testenv:docs]\nbasepython = python3.13\ncommands =\n"
                + "    sphinx-build -W -b html . _build/html\n",
                encoding="utf-8",
            )
            _, report = audit_path(project)
        self.assertEqual(report["tox_path"], "tox.ini")
        # A root tox file runs from the project root, not from '' .
        self.assertEqual(report["tox_dir"], ".")
        self.assertEqual(report["docs_python"], "3.13")

    def test_prefers_the_docs_directory(self) -> None:
        """Where both exist, the documentation copy wins."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text(
                'version: 2\nbuild:\n  os: ubuntu-24.04\n  tools:\n    python: "3.13"\n',
                encoding="utf-8",
            )
            _ = (project / "tox.ini").write_text("[testenv]\n", encoding="utf-8")
            (project / "docs").mkdir()
            _ = (project / "docs" / "tox.ini").write_text(
                "[testenv:docs]\nbasepython = python3.13\ncommands =\n"
                + "    sphinx-build -W -b html . _build/html\n",
                encoding="utf-8",
            )
            _, report = audit_path(project)
        self.assertEqual(report["tox_path"], "docs/tox.ini")

    def test_reports_nothing_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _ = (project / ".readthedocs.yaml").write_text(
                'version: 2\nbuild:\n  os: ubuntu-24.04\n  tools:\n    python: "3.13"\n',
                encoding="utf-8",
            )
            _, report = audit_path(project)
        self.assertEqual(report["tox_path"], "")
        self.assertEqual(report["tox_dir"], "")
        self.assertIn("tox-absent", codes(report))


class Rendering(unittest.TestCase):
    """The renderers survive awkward finding text."""

    def test_summary_keeps_multiline_messages_on_one_row(self) -> None:
        """A YAML parse error spans lines; a table row cannot."""
        sys.path.insert(0, str(REPO_ROOT))
        from lib.render_summary import finding_rows  # noqa: PLC0415

        report: dict[str, object] = {
            "findings": [
                {
                    "severity": "error",
                    "code": "config-unparsable",
                    "message": "Cannot parse as YAML: line 3\n  os: [unclosed\n      ^",
                }
            ]
        }
        for row in finding_rows(report):
            self.assertNotIn("\n", row)

    def test_annotation_escapes_property_separators(self) -> None:
        """A colon or comma in a property would end it early."""
        sys.path.insert(0, str(REPO_ROOT))
        from lib.render_annotations import escape_property  # noqa: PLC0415

        escaped = escape_property("a:b,c")
        self.assertNotIn(":", escaped)
        self.assertNotIn(",", escaped)
        self.assertEqual(escaped, "a%3Ab%2Cc")

    def test_annotation_message_escapes_newlines(self) -> None:
        sys.path.insert(0, str(REPO_ROOT))
        from lib.render_annotations import escape_data  # noqa: PLC0415

        self.assertEqual(escape_data("one\ntwo"), "one%0Atwo")


if __name__ == "__main__":
    _ = unittest.main(verbosity=2)
