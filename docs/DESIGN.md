# misch design

A standalone, config-driven harness that runs MISRA C:2023 static analysis on **arbitrary C projects** and guides them toward the standard. It wraps [cppcheck](https://cppcheck.sourceforge.io/) and its `misra.py` addon, driven off a real `compile_commands.json` so the checker sees exactly the include paths and defines the compiler sees.

It ships **no copyrighted MISRA material**. The rule-texts (headlines) file is bring-your-own; the harness supplies only the machinery.

## Why this exists

Firmware/controller projects each grew their own `misra_static_analysis.sh` (cppcheck + addon + platform + suppressions), and those scripts copy-pasted and diverged. Public primitive libraries can't bundle any MISRA rule material at all. This harness factors the shared machinery out once, keeps every copyrighted artifact out of the tree, and turns per-project difference into a small `misra.toml` instead of a forked script.

## Core architecture

Two ideas drive everything:

1. **`compile_commands.json` is the universal seam.** All project-specific concerns (build system, cross toolchain, flag dialects) collapse into "produce a normalised compile DB." The harness core consumes a compile DB and nothing else, so "point it at any C project" is real.
2. **One internal Finding model; outputs are pure renderers.** cppcheck is always run in XML mode and normalised into a single `Finding` model. The terminal view, JSON, the baseline diff, the scope-coverage report and the deviation record are all projections of that one model, so they can never disagree.

### Pipeline

```
resolve config
  → obtain compile DB (source: existing | meson | cmake)
  → normalise (base; ti-c2000 plugin later)
  → run cppcheck (platform + misra addon + suppressions), XML out
  → parse → Finding model
  → classify (category from BYO headlines)
  → scope-coverage check (fail on unattributed files)
  → baseline diff (optional)
  → render (terminal default; json/md/sarif on request)
  → exit code (findings → non-zero, or new-only under --baseline)
```

## Rule-texts (headlines): bring-your-own

The cppcheck misra addon runs its checks regardless; the headlines file only makes output human-readable and carries each rule's category (Mandatory / Required / Advisory). We do **not** bundle it. Resolution precedence:

```
$MISRA_RULE_TEXTS  >  misra.toml [rules].texts
```

With neither set, analysis still runs but findings are tagged `category: unknown` and a one-line notice points at `docs/rule-texts.md`. Rule **classification** is parsed from this file too, so we never ship a second copyrighted table.

## Configuration (`misra.toml`)

Everything that differs between projects is data, not shell:

```toml
[project]
scope   = ["components/", "src/"]                    # analysed
exclude = ["vendor/", "subprojects/", "src/generated/", "tests/"]

[db]
source = "meson"        # meson | cmake | existing
# Plain-Make projects: generate a DB (e.g. `bear -- make`) and use "existing".
# path = "build/compile_commands.json"               # for source = "existing"

[platform]
preset = "unix64"       # cppcheck built-in, or a custom XML path
# xml = "analysis/c2000_platform.xml"

[toolchain]
defines = []            # e.g. TI keyword neutralisation on cross targets

[rules]
texts = "${MISRA_RULE_TEXTS}"                         # BYO; env expands

[report]
outputs = ["terminal"]  # + {format="json", path="build_analysis/misra.json"}
```

## Scope: protecting non-audited content

The failure mode is silent scope creep, so **exclusion is explicit and enumerated, never implicit**:

- Central `scope`/`exclude` globs in `misra.toml`: one reviewable boundary, not scattered markers.
- Each exclude glob applies at **both** levels from one key: skip the TU (`-i`) *and* drop findings whose location falls in the excluded path (findings can leak from an excluded header included by an in-scope source).
- A **scope-coverage report** buckets every file in the compile DB into `{analysed, excluded-by-rule, unattributed}` and makes `unattributed` a **hard error**. A newly added `tests/` (or anything) must match an exclude rule or fail CI; nothing is implicitly ignored.
- The compile DB only lists translation units, so the coverage check cannot see **headers**: they enter the analysis via inclusion and findings land at their locations. A finding at an in-tree location matching neither `scope` nor `exclude` is therefore also a **hard error** -- a public-header directory left out of `scope` must not silently vanish from the audit. Findings at explicitly excluded paths, and in system/toolchain headers outside the tree, are dropped silently by design. This gate fires only when something is actually found at the unclassified location; a clean forgotten header produces nothing to drop, so full header coverage would need include-graph extraction (possible future work).

## Deviations: harvesting `cppcheck-suppress*`

Every inline suppression is a MISRA deviation and must land in the deviation record with a rationale.

- **Grammar-aware harvest** of in-scope sources: `cppcheck-suppress <id>`, bracketed `cppcheck-suppress[id1,id2]`, and `-suppress-begin/-end/-file/-macro`. Project standardises on the bracketed form.
- **Justification required** via a structured tag; the run **fails** if any inline suppression lacks one:
  ```c
  /* cppcheck-suppress misra-c2012-11.4 ; @deviation MSGRAM base is a device-fixed literal */
  ```
- **Validate ids** against the real rule set (from the headlines file) to catch typos that silently suppress nothing.
- **Staleness**: `--check-stale` runs the engine with inline suppression disabled, then flags any harvested `cppcheck-suppress` whose rule has no finding at its site. A dead deviation is an audit smell. (This cross-reference sidesteps cppcheck's `unmatchedSuppression`, which it does not reliably emit under `--project`.)
- **Merge** inline + project-level `suppressions.txt` into one categorised, justified record (scope / advisory-deviation / false-positive / inline-pointer) → the deviation report.

## Outputs

- **Default: rich terminal** (rule table grouped by category, plus summary counts; `-v` adds the per-location listing). Interpolated data is escaped and emoji substitution is off so line numbers cannot be misread as emoji. No file unless asked.
- **Opt-in file formats** via `--format`/`--output` or `[report].outputs`:
  - `json`: canonical, stable key order; substrate for baseline + deviation record; `jq`/`grep`-friendly.
  - `md`: PR summary and the audit/deviation doc.
  - `sarif`: SARIF 2.1.0 for GitHub code scanning / IDE annotations.
  - `junit`: optional CI panels (planned).
- Deterministic sort `(rule, file, line)`; artifacts under the build dir, gitignored. Exit code is format-independent.

## Honest scope

cppcheck's addon covers a subset of MISRA C:2023 (many rules are undecidable without a certified analyser). This is a **guidance + ratchet** tool, not a compliance-certification tool, which matches the goal. The engine sits behind an interface so a commercial tool (Axivion / Parasoft / PC-lint) can slot in later behind the same config and report format.

## Status

The analysis pipeline, config scaffolding (`init`), baseline/ratchet, and the deviation harvester/record are all implemented and dogfooded on the `ucrc` primitive. Planned work (cross-target platform presets, toolchain normaliser plugins, JUnit output) is tracked in the issue tracker.

## Non-goals

- Not a certified compliance tool.
- Does not bundle, reproduce, or derive MISRA rule text.
- Does not require a specific build system (compile DB is the seam).
