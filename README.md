# misch

[![CI](https://github.com/aajll/misch/actions/workflows/ci.yml/badge.svg)](https://github.com/aajll/misch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/misch)](https://pypi.org/project/misch/)
[![Python](https://img.shields.io/pypi/pyversions/misch)](https://pypi.org/project/misch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/aajll/misch/blob/master/LICENSE)

**Point `misch` at a C project and ratchet it toward MISRA C:2023.** It is a configuration-driven harness around [cppcheck](https://cppcheck.sourceforge.io/) and its `misra.py` addon. A compilation database describes the real build; `misra.toml` describes the analysis boundary and project policy.

- **Explicit scope:** every compilation unit is analysed or deliberately excluded.
- **Baseline ratchet:** existing findings remain visible while CI rejects new ones.
- **Deviation records:** suppressions require justification and can be audited for staleness.
- **Bring-your-own rule texts:** no copyrighted MISRA material is bundled.

`misch` is an adoption and guidance tool, not a compliance-certification tool. cppcheck's addon implements only a subset of MISRA C:2023 checks.

## Install

Python 3.11 or later and cppcheck with its bundled `misra.py` addon are required. cppcheck must be available on `PATH`.

```sh
uv tool install misch
```

Alternatively, use `pipx install misch` or `pip install misch`. From a checkout, use `uv sync` and prefix commands with `uv run`.

## Quick start

Run these commands from the root of the C project:

```sh
misch init --scaffold  # create misra.toml and a documented analysis/ tree
misch run              # report findings; exit 1 when findings are present
misch baseline         # explicitly accept the current finding counts
misch run --baseline   # report everything; exit 1 only for new findings
misch deviations       # validate suppressions and their justifications
```

Use `misch init` without `--scaffold` when only a configuration file is wanted. Review the generated scope, exclusions, and compilation-database source before the first run.

Rule headlines and Mandatory, Required, or Advisory categories are optional. Supply them from a licensed MISRA document through a cppcheck-format file:

```sh
export MISRA_RULE_TEXTS=/secure/path/misra-headlines.txt
```

Analysis still runs without that file and labels categories as `unknown`. See [Rule texts](https://github.com/aajll/misch/blob/master/docs/rule-texts.md) for format and licensing guidance.

## Commands

| Command | Purpose |
| --- | --- |
| `misch init` | Generate a documented `misra.toml`; add `--scaffold` for the recommended analysis asset tree. |
| `misch run` | Analyse and render terminal, JSON, or SARIF results. Add `--baseline` to fail only on findings above accepted counts. |
| `misch baseline` | Analyse and store the current finding counts as the accepted baseline. |
| `misch deviations` | Validate inline and project suppressions; optionally emit a Markdown deviation record or check staleness. |

Run `misch COMMAND --help` for command options.

Exit codes are format-independent:

- `0`: clean, no new baseline findings, or valid deviations;
- `1`: findings, new baseline findings, or invalid deviations;
- `2`: configuration, scope, compilation-database, initialization, or engine error.

## Configuration

`misch` reads `misra.toml` from the current working directory unless `-c PATH` is supplied. It does not search parent directories. Relative configuration paths are resolved from the selected file's directory.

A minimal configuration looks like this:

```toml
[project]
scope = ["src/", "include/"]
exclude = ["tests/", "vendor/"]

[db]
source = "existing"
path = "build/compile_commands.json"

[platform]
preset = "unix64"

[rules]
texts = "${MISRA_RULE_TEXTS}"
```

For a multi-platform project, put platform-specific overrides under a profile and select it for analysis:

```sh
misch run --profile aarch64
```

`--profile` selects a configuration overlay that already exists in the file; it does not set a cppcheck platform directly. Give each ratcheted profile its own `baseline.path`, since findings differ between platforms.

See the [Configuration reference](https://github.com/aajll/misch/blob/master/docs/configuration.md) for all sections, including [profile overlays](https://github.com/aajll/misch/blob/master/docs/configuration.md#profiles-platform-specific-configuration-overlays), path rules, report formats, baselines, deviations, and initialization behavior.

## Scaffold layout

`misch init --scaffold` creates:

```text
project-root/
├── misra.toml
└── analysis/
    ├── README.md
    ├── rules/README.md
    ├── deviations/misra-deviations.txt
    └── baseline/README.md
```

The configuration points at the deviations template and the future baseline path. Initialization creates neither licensed rule text nor a baseline acceptance file. If any generated target already exists, the command writes nothing unless `--force` is supplied; `--force` replaces every generated regular file.

## Documentation

- [Configuration reference](https://github.com/aajll/misch/blob/master/docs/configuration.md)
- [Architecture and limitations](https://github.com/aajll/misch/blob/master/docs/architecture.md)
- [Rule-text format and handling](https://github.com/aajll/misch/blob/master/docs/rule-texts.md)
- [Contributing](https://github.com/aajll/misch/blob/master/CONTRIBUTING.md)
- [Changelog](https://github.com/aajll/misch/blob/master/CHANGELOG.md)

## License

`misch` is distributed under the [MIT License](https://github.com/aajll/misch/blob/master/LICENSE). It contains no MISRA guideline text; users must source and handle any rule-text file according to their own licence.
