# Configuration reference

`misch` reads TOML configuration from `misra.toml` by default. Commands look for that file in the current working directory; they do not search parent directories. Use `-c PATH` to select another file.

Relative paths in the configuration are resolved from the directory containing the selected configuration file. This directory is the project root for scope matching, generated analysis artifacts, and report paths.

## Creating a configuration

Create only a documented configuration file:

```sh
misch init
```

Create the configuration and the recommended analysis asset tree:

```sh
misch init --scaffold
```

The scaffold layout is:

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

The generated configuration points project suppressions to `analysis/deviations/misra-deviations.txt` and the baseline to `analysis/baseline/misra-baseline.json`. Initialization does not create a rule-text file or baseline JSON. Supply licensed rule texts separately, and create a baseline only after reviewing findings with `misch baseline`.

Initialization checks every generated file before writing. If any target exists, nothing is written and the command exits with status 2. `--force` replaces all generated regular-file targets, including scaffold documentation and the deviations template. It does not replace a directory with a file or write through an invalid directory layout.

Initialization options such as `--db`, `--scope`, `--exclude`, `--platform`, `--rule-texts`, and `--define` prefill the generated configuration. Run `misch init --help` for the complete option list.

## `[project]`: analysis boundary

```toml
[project]
scope = ["src/", "include/"]
exclude = ["tests/", "vendor/"]
```

`scope` and `exclude` accept directory-style prefixes or glob patterns. Exclusions take precedence. Every translation unit in the compilation database must match one of the two lists or analysis fails. Finding locations within the project are checked against the same boundary.

An empty or omitted `scope` includes all paths not explicitly excluded. The generated configuration uses `scope = ["src/"]` and excludes `tests/` and `subprojects/` unless different values are supplied to `misch init`.

## `[db]`: compilation database

```toml
[db]
source = "existing" # existing | meson | cmake
path = "build/compile_commands.json"
```

- `existing` consumes `path`, defaulting to `build/compile_commands.json` relative to the project root.
- `meson` runs Meson setup when `build_analysis/compile_commands.json` is absent.
- `cmake` configures CMake with compilation-database export when that file is absent.

For another build system, generate `compile_commands.json` separately and use `existing`. For example, an interception tool such as Bear can generate one for a Make-based build.

`misch` writes a path-normalised copy to `build_analysis/compile_commands.normalised.json` before invoking cppcheck.

## `[platform]`: cppcheck target model

Use a cppcheck built-in platform:

```toml
[platform]
preset = "unix64"
```

Or provide a custom cppcheck platform description:

```toml
[platform]
xml = "analysis/platform.xml"
```

`xml` takes precedence when both values are present. A relative XML path is resolved from the directory containing `misra.toml`. The default platform is `unix64`.

## `[toolchain]`: additional defines

```toml
[toolchain]
defines = ["FEATURE_X=1", "__interrupt="]
```

Each entry is passed to cppcheck as a `-D` definition.

## `[rules]`: optional licensed headlines

```toml
[rules]
texts = "${MISRA_RULE_TEXTS}"
```

The rule-text file adds headlines and Mandatory, Required, or Advisory categories. Analysis works without it and labels categories as `unknown`.

A readable path from the `MISRA_RULE_TEXTS` environment variable takes precedence over `[rules].texts`. Environment variables in the configured value are expanded. Relative paths are resolved from the directory containing `misra.toml`. See [Rule texts](rule-texts.md) for format and licensing guidance.

## `[report]`: default run outputs

```toml
[report]
outputs = [
  "terminal",
  { format = "json", path = "build_analysis/misra.json" },
  { format = "sarif", path = "build_analysis/misra.sarif" },
]
```

Supported `misch run` formats are `terminal`, `json`, and `sarif`. Configured relative output paths are resolved from the directory containing `misra.toml`. Command-line `--format` options replace the configured output list for that run and may be repeated. `--output PATH` overrides the path used for a requested file format. Terminal output has no file path.

Without configuration, terminal output is used.

## `[deviations]`: project suppressions

```toml
[deviations]
suppressions = "analysis/deviations/misra-deviations.txt"
```

The optional file uses cppcheck's suppressions-list syntax. Each active entry must be preceded by comment lines that provide its justification. Prefer the narrowest practical file or line scope and use a justified inline `cppcheck-suppress` for a single source location.

`misch deviations` combines project entries with inline suppressions, checks justifications, and validates MISRA identifiers when rule headlines are available. Add `--format md` to write a Markdown record and `--check-stale` to compare line-level inline suppressions with an unsuppressed analysis run.

## `[baseline]`: accepted findings

```toml
[baseline]
path = "analysis/baseline/misra-baseline.json"
```

The default without this section is `misra-baseline.json` in the project root. `misch baseline` analyses the project and writes the accepted fingerprint counts. `misch run --baseline` continues to report all findings but exits with status 1 only for findings above the accepted counts.

Use `misch baseline --baseline-file PATH` to override the destination for one baseline operation.

## Exit codes

Exit status is independent of output format:

- `0`: clean analysis, no new findings in baseline mode, or a valid deviation record.
- `1`: findings, new findings in baseline mode, or invalid deviations.
- `2`: configuration, compilation-database, scope, initialization, or engine error.
