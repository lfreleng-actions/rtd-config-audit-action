# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Audit a repository's Read the Docs configuration for policy compliance.

Parses ``.readthedocs.yaml`` as YAML rather than by pattern matching, so a
key nested under ``build.tools.python`` never gets confused with a
similarly named key elsewhere in the document. Reconciles the Python
version the documentation build declares against the version the tox
environment pins, and reports findings as JSON for the calling action.

The policy arrives entirely through the command line. With no policy
flags the audit checks only what every project shares: that the config
parses, that it declares a build image and a documentation Python
version, and that the version still receives upstream support.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from lib.rtd_checks import (
    check_build_os,
    check_files,
    check_python_eol,
    check_retired_keys,
    check_rtd_python,
    check_sphinx_flags,
    check_version_key,
    load_config,
    read_tox_docs_python,
    resolve_python,
)
from lib.rtd_model import (
    CONFIG_NAMES,
    SEVERITY_ERROR,
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    TOX_NAMES,
    Audit,
    find_first,
    relative_to,
    resolve_under,
    split_list,
)


@dataclass(frozen=True)
class Settings:
    """Policy and locations for one audit run."""

    path_prefix: str = "."
    config_file: str = ""
    tox_file: str = ""
    required_files: str = ""
    forbidden_files: str = ""
    build_os: str = ""
    python_version: str = ""
    supported_versions: str = ""
    default_python: str = "3.13"
    eol_behaviour: str = "warn"
    linkcheck_policy: str = "warn"
    require_sphinx_strict: bool = False
    fail_on_warning: bool = False


@dataclass
class Resolved:
    """Values the audit determines while inspecting a project."""

    config_found: bool = False
    config_path: str = ""
    tox_path: str = ""
    tox_dir: str = ""
    build_os: str = ""
    rtd_python: str = ""
    tox_python: str = ""
    docs_python: str = ""
    python_mismatch: bool = False
    python_eol: bool = False

    def as_dict(self) -> dict[str, object]:
        """Render the resolved values for JSON output."""
        return {
            "config_found": self.config_found,
            "config_path": self.config_path,
            "tox_path": self.tox_path,
            "tox_dir": self.tox_dir,
            "build_os": self.build_os,
            "rtd_python": self.rtd_python,
            "tox_python": self.tox_python,
            "docs_python": self.docs_python,
            "python_mismatch": self.python_mismatch,
            "python_eol": self.python_eol,
        }


@dataclass
class Report:
    """A complete audit outcome."""

    resolved: Resolved
    audit: Audit
    settings: Settings

    @property
    def errors(self) -> int:
        """Count findings at error severity."""
        return self.audit.count(SEVERITY_ERROR)

    @property
    def warnings(self) -> int:
        """Count findings at warning severity."""
        return self.audit.count(SEVERITY_WARNING)

    @property
    def passed(self) -> bool:
        """Report whether the audit avoided a failing finding."""
        if self.errors:
            return False
        return not (self.settings.fail_on_warning and self.warnings)

    def as_dict(self) -> dict[str, object]:
        """Render the whole report for JSON output."""
        return {
            **self.resolved.as_dict(),
            "errors": self.errors,
            "warnings": self.warnings,
            "passed": self.passed,
            "findings": [f.as_dict() for f in self.audit.findings],
        }


def build_parser() -> argparse.ArgumentParser:
    """Define the command line."""
    parser = argparse.ArgumentParser(description="Audit a Read the Docs configuration.")
    _ = parser.add_argument("--path-prefix", default=".", help="Project root directory")
    _ = parser.add_argument(
        "--config-file", default="", help="Path to the Read the Docs config"
    )
    _ = parser.add_argument(
        "--tox-file", default="", help="Path to the tox file holding the docs env"
    )
    _ = parser.add_argument(
        "--required-files", default="", help="Files the project must ship"
    )
    _ = parser.add_argument(
        "--forbidden-files", default="", help="Files the project must not ship"
    )
    _ = parser.add_argument("--build-os", default="", help="Expected build.os value")
    _ = parser.add_argument(
        "--python-version", default="", help="Expected build.tools.python value"
    )
    _ = parser.add_argument(
        "--supported-versions", default="", help="Supported Python versions"
    )
    _ = parser.add_argument(
        "--default-python", default="3.13", help="Fallback docs Python version"
    )
    _ = parser.add_argument(
        "--eol-behaviour",
        default="warn",
        choices=("warn", "fail", "ignore"),
        help="How to treat an end-of-life Python version",
    )
    _ = parser.add_argument(
        "--linkcheck-policy",
        default="warn",
        choices=("warn", "fail", "ignore"),
        help="How to treat a linkcheck environment that sets -W",
    )
    _ = parser.add_argument(
        "--require-sphinx-strict",
        action="store_true",
        help="Expect '-W' on documentation builds",
    )
    _ = parser.add_argument(
        "--fail-on-warning", action="store_true", help="Treat warnings as failures"
    )
    return parser


def settings_from(namespace: argparse.Namespace) -> Settings:
    """Convert parsed arguments into typed settings."""
    values = cast("dict[str, object]", vars(namespace))

    def text(key: str) -> str:
        value = values.get(key)
        return "" if value is None else str(value)

    return Settings(
        path_prefix=text("path_prefix"),
        config_file=text("config_file"),
        tox_file=text("tox_file"),
        required_files=text("required_files"),
        forbidden_files=text("forbidden_files"),
        build_os=text("build_os"),
        python_version=text("python_version"),
        supported_versions=text("supported_versions"),
        default_python=text("default_python"),
        eol_behaviour=text("eol_behaviour"),
        linkcheck_policy=text("linkcheck_policy"),
        require_sphinx_strict=values.get("require_sphinx_strict") is True,
        fail_on_warning=values.get("fail_on_warning") is True,
    )


def audit_config(
    config_path: Path, root: Path, settings: Settings, resolved: Resolved, audit: Audit
) -> None:
    """Run every check that reads the Read the Docs configuration."""
    location = relative_to(config_path, root)
    resolved.config_path = location

    config = load_config(config_path, audit)
    if not config:
        return

    check_version_key(config, location, audit)
    check_retired_keys(config, location, audit)
    resolved.build_os = check_build_os(config, location, settings.build_os, audit)
    resolved.rtd_python = check_rtd_python(
        config, location, settings.python_version, audit
    )


def audit_tox(root: Path, settings: Settings, resolved: Resolved, audit: Audit) -> None:
    """Run every check that reads the tox file, when one exists."""
    tox_path: Path | None
    if settings.tox_file:
        tox_path = resolve_under(root, settings.tox_file)
        looked_for = settings.tox_file
    else:
        tox_path = find_first(root, TOX_NAMES)
        looked_for = ", ".join(TOX_NAMES)

    if tox_path is None or not tox_path.is_file():
        audit.add(
            SEVERITY_NOTICE,
            "tox-absent",
            f"No tox file found (looked for {looked_for}); skipped tox checks",
        )
        return

    # Report where the tox file lives so a caller running the same
    # documentation build need not repeat this search, and cannot
    # disagree with it.
    resolved.tox_path = relative_to(tox_path, root)
    # tox runs from the directory holding its configuration, and a
    # caller cannot take a dirname inside a workflow expression.
    parent = str(Path(resolved.tox_path).parent)
    resolved.tox_dir = "." if parent == "." else parent
    resolved.tox_python = read_tox_docs_python(tox_path, root, audit)
    check_sphinx_flags(
        tox_path, root, settings.require_sphinx_strict, settings.linkcheck_policy, audit
    )


def run_audit(settings: Settings) -> Report:
    """Perform the audit and return its report."""
    audit = Audit()
    resolved = Resolved()
    root = Path(settings.path_prefix).resolve()

    if not root.is_dir():
        audit.add(
            SEVERITY_ERROR, "path-missing", f"Project directory does not exist: {root}"
        )
        return Report(resolved, audit, settings)

    config_path: Path | None
    if settings.config_file:
        config_path = resolve_under(root, settings.config_file)
        looked_for = settings.config_file
    else:
        config_path = find_first(root, CONFIG_NAMES)
        looked_for = ", ".join(CONFIG_NAMES)

    if config_path is None or not config_path.is_file():
        audit.add(
            SEVERITY_ERROR,
            "config-missing",
            f"No Read the Docs configuration found (looked for {looked_for})",
        )
        return Report(resolved, audit, settings)

    resolved.config_found = True
    audit_config(config_path, root, settings, resolved, audit)
    audit_tox(root, settings, resolved, audit)

    before = audit.count(SEVERITY_WARNING)
    resolved.docs_python = resolve_python(
        resolved.tox_python,
        resolved.rtd_python,
        settings.default_python,
        audit,
    )
    resolved.python_mismatch = audit.count(SEVERITY_WARNING) > before

    check_files(
        root,
        split_list(settings.required_files),
        split_list(settings.forbidden_files),
        audit,
    )

    resolved.python_eol = check_python_eol(
        resolved.docs_python,
        split_list(settings.supported_versions.replace(" ", "\n")),
        settings.eol_behaviour,
        audit,
    )

    return Report(resolved, audit, settings)


def main(argv: list[str] | None = None) -> int:
    """Run the audit and print a JSON report to stdout."""
    settings = settings_from(build_parser().parse_args(argv))
    report = run_audit(settings)
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
