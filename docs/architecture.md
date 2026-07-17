# Architecture

`misch` is a configuration-driven harness for running cppcheck's MISRA addon on C projects. It obtains or consumes a compilation database, normalises findings into stable internal models, enforces an explicit analysis boundary, and renders results for developers and CI.

It does not contain MISRA guideline text. Rule headlines and categories are supplied separately from a licensed source when needed.

## Design principles

### The compilation database is the build-system boundary

`compile_commands.json` captures the source files, include paths, defines, and compiler flags used by a project. `misch` can consume an existing database or ask Meson or CMake to generate one. It then rewrites source paths to absolute paths in a normalised copy so downstream analysis is independent of how the build system represented them.

### Analysis scope is explicit

Each translation unit in the compilation database must match either `[project].scope` or `[project].exclude`. An unmatched file is a configuration error rather than an implicit exclusion.

The compilation database contains translation units, not every included header. After analysis, `misch` applies the same boundary to finding locations. A finding in a project file that matches neither scope nor exclusions is also an error. Findings in explicitly excluded paths and external system or toolchain headers are omitted.

This model prevents known files and reported locations from silently falling outside the audit boundary. It cannot prove coverage of a header that produces no finding and is absent from the compilation database.

### Findings have one analysis model

cppcheck output is parsed from XML into `Finding` objects. Terminal, JSON, SARIF, and baseline comparison all consume those normalised findings rather than independently parsing engine output. Stable sorting and baseline fingerprints make machine-readable results reproducible.

Deviation auditing has a separate `Deviation` model because suppressions describe accepted exceptions rather than findings. It harvests inline suppressions and project suppression files, validates their metadata, and can compare line-level inline suppressions with an unsuppressed analysis run.

## Analysis pipeline

```text
load and resolve misra.toml
  → obtain compile_commands.json (Meson, CMake, or existing)
  → normalise compilation-database paths
  → classify translation units by scope
  → run cppcheck and its misra.py addon
  → parse XML into findings
  → classify MISRA findings with optional rule headlines
  → enforce scope at finding locations
  → compare with the baseline when requested
  → render terminal, JSON, or SARIF output
  → return a format-independent exit status
```

Project suppressions and inline `cppcheck-suppress` directives are passed to cppcheck during a normal run. `misch deviations --check-stale` performs an additional run without those suppressions so it can identify line-level inline suppressions that no longer hide a corresponding finding.

## Baseline model

A baseline stores counts keyed by line-independent finding fingerprints. In baseline mode, all current findings remain visible, but the command fails only when current counts exceed accepted counts. This supports incremental adoption without treating existing findings as permanently invisible.

Creating or replacing a baseline is an explicit acceptance action performed by `misch baseline`; initialization never accepts findings automatically.

## Rule-text boundary

cppcheck's addon can detect rules without a rule-text file. `misch` uses an optional bring-your-own file to add guideline headlines and Mandatory, Required, or Advisory categories. Without it, analysis still runs and uses the `unknown` category.

The environment variable `MISRA_RULE_TEXTS` takes precedence over `[rules].texts`. The project ships no MISRA guideline text and does not derive a substitute rule table. See [Rule texts](rule-texts.md) for the expected format and handling guidance.

## Limitations and non-goals

- cppcheck's addon covers only a subset of MISRA C:2023; `misch` is not a compliance-certification tool.
- Scope checks cover compilation-database entries and finding locations, not every header reachable through the include graph.
- Staleness checking is limited to line-level inline suppressions that can be correlated with an unsuppressed finding.
- `misch` does not bundle, reproduce, or derive copyrighted MISRA guideline text.
- `misch` does not require a particular build system when an existing compilation database is available.
