# misch

[![CI](https://github.com/aajll/misch/actions/workflows/ci.yml/badge.svg)](https://github.com/aajll/misch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/misch)](https://pypi.org/project/misch/)
[![Python](https://img.shields.io/pypi/pyversions/misch)](https://pypi.org/project/misch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Point it at any C project and ratchet it toward MISRA C:2023.** `misch` (MISra-CHeck) is a config-driven static-analysis harness: it wraps [cppcheck](https://cppcheck.sourceforge.io/) and its `misra.py` addon, drives them off a real `compile_commands.json`, and turns per-project difference into a small `misra.toml` instead of a forked shell script.

- **Bring-your-own rule texts:** ships no copyrighted MISRA material (see [`docs/rule-texts.md`](docs/rule-texts.md)).
- **Baseline / ratchet:** adopt MISRA on an existing codebase without a day-one wall of findings; fail CI only on _new_ ones.
- **Deviation records:** harvests every `cppcheck-suppress`, enforces a justification, and emits an audit-ready record.
- **No silent scope creep:** every file is consciously analysed or excluded, or the run fails.

> `misch` is a **guidance + ratchet** tool, not a compliance-certification tool: cppcheck's addon covers a subset of MISRA C:2023. The engine sits behind an interface so a certified analyser can slot in later. See [`docs/DESIGN.md`](docs/DESIGN.md).

## Install

Requires Python ≥ 3.11 and `cppcheck` (with its bundled `misra.py` addon) on `PATH`. Tested with cppcheck 2.21; any recent release with the `misra.py` addon should work.

```sh
uv tool install misch        # or: pipx install misch / pip install misch
```

From a checkout: `uv tool install .` (or `uv sync && uv run misch ...`).

## Quick start

```sh
cd /path/to/your/c-project
misch init --scaffold      # config + documented analysis/ asset tree
# Or use `misch init` when you only want the config file.
misch run                  # analyse; categorised report; non-zero exit on findings
misch baseline             # accept current findings as the baseline
misch run --baseline       # from now on, fail only on NEW findings
misch deviations           # audit every suppression + its justification
```

Bring your own rule texts (optional, for headlines + Mandatory/Required/Advisory):

```sh
export MISRA_RULE_TEXTS=/path/to/misra_c_2023_headlines_for_cppcheck.txt
```

A run against a project with findings looks like this (headline text comes from your own rule-texts file):

```text
──────────────────────────────── MISRA analysis ────────────────────────────────
scope: 1 analysed  0 excluded  files with findings: 1

Findings by rule
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Rule             ┃ Category ┃ Count ┃ Headline                               ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ misra-c2012-8.4  │ required │     2 │ Headline from your licensed MISRA copy │
│ misra-c2012-17.7 │ required │     1 │ Headline from your licensed MISRA copy │
│ misra-c2012-21.6 │ required │     1 │ Headline from your licensed MISRA copy │
│ misra-c2012-15.5 │ advisory │     1 │ Headline from your licensed MISRA copy │
│ misra-c2012-8.7  │ advisory │     1 │ Headline from your licensed MISRA copy │
└──────────────────┴──────────┴───────┴────────────────────────────────────────┘

Run with -v/--verbose for the per-location listing.

6 MISRA finding(s): 4 required  2 advisory
```

## Commands

| Command            | Purpose                                                                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `misch init`       | Generate a commented `misra.toml`; add `--scaffold` for a documented `analysis/` asset tree. Other flags (`--db`, `--scope`, `--exclude`, `--platform`, `--rule-texts`, …) pre-fill the config. |
| `misch run`        | Analyse. Rule table + summary (add `-v` for per-location detail, or `--format json`/`--format sarif`). Exit 1 on findings; `--baseline` fails only on new ones. |
| `misch baseline`   | Snapshot the current findings as the accepted baseline.                                                                                                         |
| `misch deviations` | Harvest `cppcheck-suppress*`, enforce justifications, validate rule ids, emit a Markdown deviation record (`--format md`).                                      |

Exit codes are CI-friendly and format-independent: `0` clean (or no new findings under `--baseline`), `1` findings (or unjustified/unknown deviations), `2` config, compile-DB, scope, or engine error.

## Configuration

`misch init` writes a documented `misra.toml`; the essentials:

```toml
[project]
scope   = ["src/", "include/"]                 # analysed
exclude = ["tests/", "subprojects/"]           # explicitly out of scope

[db]
source = "meson"                               # meson | cmake | existing
# Plain-Make projects: generate a DB (e.g. `bear -- make`) and use "existing".

[platform]
preset = "unix64"                              # cppcheck built-in, or [platform].xml

[rules]
texts = "${MISRA_RULE_TEXTS}"                  # bring-your-own; optional
```

## Scaffolded project layout

`misch init` creates only the requested config file. For a new integration,
`misch init --scaffold` also creates a conventional, documented asset tree:

```text
project-root/
├── misra.toml
└── analysis/
    ├── README.md
    ├── rules/
    │   └── README.md
    ├── deviations/
    │   └── misra-deviations.txt
    └── baseline/
        └── README.md
```

The generated config points project-level suppressions and the future baseline
at this tree. It continues to use `$MISRA_RULE_TEXTS` (or `--rule-texts`) for
licensed rule headlines; no rule text is generated or bundled. The baseline
JSON is also not created during initialization: after reviewing the first run,
use `misch baseline` to explicitly accept the current findings.

Initialization is non-destructive by default. If any generated target already
exists, no files are written. `--force` explicitly replaces every generated
target, including scaffold documentation and deviations, so review the listed
paths before using it.

## How it works

`compile_commands.json` is the universal seam: every build-system and toolchain concern collapses into "produce a normalised compile DB". A single internal Finding model backs every output (terminal, JSON, baseline diff, deviation record), so they can never disagree. Full architecture and rationale in [`docs/DESIGN.md`](docs/DESIGN.md).

## Documentation

- [`docs/DESIGN.md`](docs/DESIGN.md): architecture, the scope and deviation strategy.
- [`docs/rule-texts.md`](docs/rule-texts.md): bringing your own MISRA headlines.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): dev setup, tests, adding engines/normalisers.
- [`CHANGELOG.md`](CHANGELOG.md): release notes.

## License

MIT (see [`LICENSE`](LICENSE)). `misch` contains and ships no MISRA material; rule texts are bring-your-own from your licensed copy.
