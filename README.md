<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 📄 ReadTheDocs Configuration Audit

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/rtd-config-audit-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![pre-commit.ci status badge]][pre-commit.ci results page] [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/rtd-config-audit-action/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/rtd-config-audit-action)
<!-- prettier-ignore-end -->

## rtd-config-audit-action

Checks a repository's ReadTheDocs setup for policy compliance, and
reports the Python version the documentation build should use.

The action parses `.readthedocs.yaml` as YAML. Pattern matching against
the raw text cannot tell `build.tools.python` apart from a `python:` key
elsewhere in the document, and that ambiguity has produced false results
in earlier tooling.

Policy arrives through inputs. With no configuration the action checks
what every project shares:

- The configuration parses and declares schema version 2.
- It declares `build.os` and `build.tools.python`.
- It avoids keys ReadTheDocs no longer honours.
- The documentation Python version still receives upstream support.
- The tox documentation environment agrees with ReadTheDocs about the
  Python version.

## Usage Example

<!-- markdownlint-disable MD046 -->

```yaml
steps:
  - name: "Audit ReadTheDocs configuration"
    id: rtd-audit
    uses: lfreleng-actions/rtd-config-audit-action@main

  - name: "Set up the documentation interpreter"
    uses: actions/setup-python@v6
    with:
      python-version: ${{ steps.rtd-audit.outputs.docs_python }}
```

<!-- markdownlint-enable MD046 -->

Supplying a project policy:

<!-- markdownlint-disable MD046 -->

```yaml
steps:
  - name: "Audit ReadTheDocs configuration"
    uses: lfreleng-actions/rtd-config-audit-action@main
    with:
      build_os: "ubuntu-24.04"
      required_files: |
        docs/index.rst
        docs/conf.py
        docs/requirements-docs.txt
      forbidden_files: "docs/conf.yaml"
      eol_behaviour: "fail"
```

<!-- markdownlint-enable MD046 -->

## Inputs

<!-- markdownlint-disable MD013 -->

| Input                   | Required | Default  | Description                                                             |
| ----------------------- | -------- | -------- | ----------------------------------------------------------------------- |
| `path_prefix`           | False    | `.`      | Directory location containing project code                              |
| `config_file`           | False    |          | Path to the config; empty searches `.readthedocs.yaml` then `.yml`      |
| `tox_file`              | False    |          | Path to the tox file; empty searches `docs/tox.ini` then `tox.ini`      |
| `required_files`        | False    |          | Files the project must ship; empty disables the check                   |
| `forbidden_files`       | False    |          | Files the project must not ship; empty disables the check               |
| `build_os`              | False    |          | Expected `build.os`; empty accepts whatever the project declares        |
| `python_version`        | False    |          | Expected `build.tools.python`; empty accepts any supported version      |
| `default_python`        | False    | `3.13`   | Version to assume when the project declares none                        |
| `eol_behaviour`         | False    | `warn`   | Treatment of an end-of-life Python version: `warn`, `fail` or `ignore`  |
| `linkcheck_policy`      | False    | `warn`   | Treatment of a linkcheck environment setting `-W`                       |
| `require_sphinx_strict` | False    | `false`  | Expect `-W` on documentation builds                                     |
| `fail_on_warning`       | False    | `false`  | Fail the action when the audit reports warnings                         |
| `offline_mode`          | False    | `false`  | Skip the network lookup of supported Python versions                    |
| `summary`               | False    | `true`   | Write a report to the workflow step summary                             |

<!-- markdownlint-enable MD013 -->

Both `required_files` and `forbidden_files` accept newline or comma
separated entries.

## Outputs

<!-- markdownlint-disable MD013 -->

| Output            | Description                                                      |
| ----------------- | ---------------------------------------------------------------- |
| `passed`          | Whether the audit completed without a failing finding            |
| `config_found`    | Whether the action located a ReadTheDocs config                  |
| `config_path`     | Path of the config the action inspected                          |
| `docs_python`     | Python version the documentation build should use                |
| `tox_python`      | Version pinned by the tox docs environment                       |
| `rtd_python`      | Version declared by `build.tools.python`                         |
| `python_mismatch` | Whether the tox and ReadTheDocs versions diverge                 |
| `python_eol`      | Whether the documentation Python version has reached end of life |
| `build_os`        | The `build.os` value the project declares                        |
| `errors`          | Count of findings at error severity                              |
| `warnings`        | Count of findings at warning severity                            |
| `findings_json`   | Complete audit report as a JSON string                           |

<!-- markdownlint-enable MD013 -->

## Link checking and `-W`

Sphinx treats `-W` as "turn warnings into errors". That setting suits a
documentation build, where a broken cross-reference marks a genuine
fault. A link check gains nothing from it.

External sites increasingly refuse automated requests, classing them as
unwanted crawler traffic. A link check that fails the build on an
unreachable URL reports another site's bot policy as a fault in the
change under review, and the author can do nothing about it.

The action reports `-W` on a linkcheck builder as a warning by default.
Set `linkcheck_policy: fail` to block such a configuration, or `ignore`
to say nothing. The matching flag for documentation builds,
`require_sphinx_strict`, stays off by default and applies to builders
other than linkcheck.

## Python version resolution

The action reads two sources and reconciles them:

1. `basepython` in the `[testenv:docs]` section of the tox file, falling
   back to `[testenv]`.
2. `build.tools.python` in the ReadTheDocs configuration.

`docs_python` reports whichever tox pins, because tox drives the
verification job. When the two disagree the action emits a
`python-mismatch` warning: a verification job running a different
interpreter from the published build tests something other than what
readers receive.

End-of-life data comes from
[python-supported-versions-action][py-versions], so a single source holds
the release calendar for the whole organisation.

## Findings

<!-- markdownlint-disable MD013 -->

| Code                     | Severity | Meaning                                                    |
| ------------------------ | -------- | ---------------------------------------------------------- |
| `config-missing`         | Error    | No ReadTheDocs configuration found                         |
| `config-unparsable`      | Error    | The configuration is not valid YAML                        |
| `config-empty`           | Error    | The configuration file holds nothing                       |
| `config-not-mapping`     | Error    | The top level is not a mapping                             |
| `version-missing`        | Error    | The configuration omits `version`                          |
| `version-unsupported`    | Error    | The configuration declares a version other than 2          |
| `build-os-missing`       | Error    | The configuration omits `build.os`                         |
| `python-missing`         | Error    | The configuration omits `build.tools.python`               |
| `required-file-missing`  | Error    | A file named by policy is absent                           |
| `forbidden-file-present` | Error    | A file named by policy still exists                        |
| `retired-key`            | Warning  | The configuration uses a key ReadTheDocs ignores           |
| `build-os-mismatch`      | Warning  | `build.os` differs from the configured policy              |
| `python-policy-mismatch` | Warning  | `build.tools.python` differs from the configured policy    |
| `python-mismatch`        | Warning  | The tox and ReadTheDocs Python versions diverge            |
| `python-eol`             | Warning  | The documentation Python version has reached end of life   |
| `linkcheck-strict`       | Warning  | A linkcheck environment sets `-W`                          |
| `sphinx-not-strict`      | Warning  | A documentation build omits `-W` while policy expects it   |
| `sphinx-absent`          | Warning  | The tox file runs no `sphinx-build` command                |
| `tox-unparsable`         | Warning  | The tox file does not parse                                |
| `tox-absent`             | Notice   | No tox file found; the action skipped the tox checks       |

<!-- markdownlint-enable MD013 -->

Errors fail the action. Warnings fail it when `fail_on_warning` is
`true`. Every finding also appears as a workflow annotation.

## Testing

The audit runs as a standalone script, so the suite needs no runner:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Fixture projects under `tests/fixtures/` cover a compliant project, a
configuration using retired keys, a Python version mismatch, a strict
linkcheck environment, unparsable YAML and a project with no config.

[py-versions]: https://github.com/lfreleng-actions/python-supported-versions-action

[pre-commit.ci results page]: https://results.pre-commit.ci/latest/github/lfreleng-actions/rtd-config-audit-action/main
<!-- markdownlint-disable-next-line MD013 -->
[pre-commit.ci status badge]: https://results.pre-commit.ci/badge/github/lfreleng-actions/rtd-config-audit-action/main.svg
