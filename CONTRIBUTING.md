# Contributing to misch

`misch` is a small, focused tool: a config-driven MISRA C:2023 harness around cppcheck. Contributions are held to that bar: dependency-light, tested, and honest about what the analysis can and cannot prove.

## Dev setup

```sh
uv sync                     # install the project + dev deps into .venv
uv run misch --help
uv run pytest -q            # unit tests (no cppcheck needed)
uv run ruff check .         # lint
```

`cppcheck` (with its bundled `misra.py` addon) must be on `PATH` for the `run`/`baseline` commands and any end-to-end test.

## Architecture in one paragraph

`compile_commands.json` is the universal seam; a single internal `Finding` model backs every output. The pipeline is `resolve config → obtain + normalise compile DB → scope-check → cppcheck (XML) → Finding model → classify → render`. See [`docs/DESIGN.md`](docs/DESIGN.md) for the full picture. Keep renderers pure (model in, bytes out) so the terminal, JSON, baseline, and deviation views can never disagree.

## Where things live

- `src/misch/cli.py`: argument parsing and command handlers.
- `config.py`: `misra.toml` loading and path/rule-texts resolution.
- `db/`: compile-DB resolution, normalisation, and scope classification.
- `engine/cppcheck.py`: the only place that shells out to cppcheck; parses XML into Findings.
- `report/`: the `Finding` model, headlines parser, baseline, deviation harvester, and renderers.
- `scaffold.py`: the `init` config template.

## Adding an engine or a DB normaliser

- **A new engine** implements the same contract as `engine/cppcheck.run`: take the config + compile DB, return `list[Finding]`. Nothing downstream should know which engine produced a finding.
- **A DB normaliser** (e.g. a cross-toolchain flag translator) transforms the compile DB before analysis. Keep it optional and selected by config, so the common native case needs none.

## Rules of the house

- **No bundled MISRA material.** The rule-texts file is bring-your-own and gitignored; never commit it or paraphrase copyrighted rule text into the tree.
- **Python ≥ 3.11**, standard library first. New runtime dependencies need a good reason.
- **Determinism.** Sort output by `(rule, file, line)`; keep JSON key order stable. No non-deterministic tests.
- **Be honest about coverage.** cppcheck checks a subset of MISRA C:2023; if a feature is best-effort (e.g. staleness), say so in the docstring and the docs rather than overclaim.
- Run `ruff check .` and `pytest` before opening a PR. Add a test for every bug fix and every feature.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `doc:`, `test:`, `chore:`, `refactor:`). Explain _why_ in the body, not _what_ the diff already shows.
