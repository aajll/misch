# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-07-22

### Added

- Added multi-platform configuration profiles. A single `misra.toml` can define `[profiles.<name>]` overlays, selected with `--profile` on `misch run`, `misch baseline`, and `misch deviations`, so a project with several architecture targets no longer needs a separate config file per target. A profile deep-merges nested tables over the base configuration, replaces scalars and lists, and extends the documented list settings (`project.scope`, `project.exclude`, `toolchain.defines`, `report.outputs`) through an `append_` prefix. Profiles are validated against the supported schema before merging, and an unknown profile name reports the available profiles.

### Improved

- Rejected configurations that set both `platform.preset` and `platform.xml`, which are mutually exclusive, instead of silently preferring one. A profile's `[platform]` now replaces the base platform wholesale, so a target can switch between a cppcheck preset and a platform XML.

## [0.2.0] - 2026-07-17

### Added

- Added `misch init --scaffold`, an opt-in initializer for a documented `analysis/` tree containing rule-text guidance, a project deviations template, and baseline workflow guidance while keeping `misra.toml` at the project root.

### Improved

- Made initialization non-destructive by preflighting every generated target before writing. Existing files abort the entire operation unless `--force` is supplied, and invalid path layouts now produce controlled errors instead of partial output or tracebacks.
- Rejected symbolic-link initialization targets and symlinked scaffold paths, including with `--force`, so generated files cannot be redirected outside the intended project layout.
- Scaffolded configurations preserve explicit `--rule-texts` settings, point project suppressions and the future baseline at their `analysis/` paths, and intentionally generate neither licensed MISRA text nor a baseline acceptance file.
- Moved scaffold guidance into packaged, directly editable templates that are shared by editable installs, source distributions, and wheels.
- Restructured the documentation into a concise PyPI/GitHub README, a configuration reference, an architecture explanation, focused rule-text guidance, and contributor documentation without project-external status or roadmap material.
- Made custom platform XML and configured report-output paths resolve relative to the selected `misra.toml`, consistent with other configured paths.
- Validated custom platform XML and report-output paths as non-empty strings so invalid values produce a controlled configuration error instead of a traceback or later filesystem failure.
- Added a live-updating status spinner and progress message (`Running analysis...`) during long-running engine executions (`misch run`, `misch baseline`, and `misch deviations --check-stale`) to provide feedback during large project analysis.
- Refactored versioning to use `importlib.metadata` as a single source of truth (from `pyproject.toml`).

## [0.1.0] - 2026-07-07

First public release.

### Added

- `misch run`: analyse a C project via cppcheck + the `misra.py` addon, driven off a normalised `compile_commands.json`. Categorised rich-terminal report (rule table + summary by default; `-v`/`--verbose` adds the per-location listing) and deterministic JSON output; non-zero exit on findings. Interpolated paths and messages are escaped and emoji substitution is disabled, so a `:100:` line number is never rendered as an emoji.
- Compile-DB sources: `existing`, `meson`, and `cmake` (paths normalised to absolute up front). Plain-Make projects can generate a DB with an interceptor (e.g. `bear -- make`) and use `existing`.
- Scope control: `scope` / `exclude` globs with a hard failure on any unattributed file, so nothing is ever silently ignored.
- Bring-your-own MISRA rule texts (`$MISRA_RULE_TEXTS` > `[rules].texts`); headlines and Mandatory/Required/Advisory categories parsed from them. No copyrighted material is bundled.
- `misch init`: generate a commented `misra.toml`, with flags to pre-fill every section.
- `misch baseline` and `misch run --baseline`: count-based ratchet keyed on a line-independent fingerprint; report all findings but fail only on new ones.
- `misch deviations`: grammar-aware harvest of every `cppcheck-suppress*` form, justification enforcement, rule-id validation against the headlines, project suppressions-file parsing, and a terminal + Markdown deviation record. `--check-stale` cross-references an unsuppressed run to flag suppressions that hide nothing.
- SARIF 2.1.0 output (`--format sarif`) for GitHub code scanning and IDE annotations, with MISRA category mapped to error/warning levels.
- Coloured `--help` output via `rich-argparse` (auto-disables when stdout is not a TTY or `NO_COLOR` is set).
- `misch --version`.
- Release pipeline: `uv build` in CI, and a tag-triggered GitHub Actions workflow publishing to PyPI via trusted publishing.

### Fixed

- Findings at in-tree locations matching neither `scope` nor `exclude` are now a hard error (exit 2, offending files listed) instead of being silently dropped. The compile DB only lists translation units, so the scope-coverage gate never sees headers; previously a public-header directory left out of `scope` simply vanished from the audit. Findings at explicitly excluded paths and in system headers outside the tree are still dropped silently by design.
- Error messages no longer lose TOML section names to rich markup: `[project].scope`, `[project].exclude`, and `[rules].texts` were being parsed as style tags and dropped from the scope-error and missing-rule-texts notices.
- `misch run` now applies the project suppressions file (`[deviations].suppressions`) to the analysis via cppcheck `--suppressions-list`, so a project-level deviation actually silences the findings it names. Previously the file was only harvested into the deviation record by `misch deviations`; findings it covered still surfaced and failed the run. The suppressions file is dropped on the `--check-stale` pass (alongside inline `cppcheck-suppress` comments) so staleness detection still sees what each entry hides.
