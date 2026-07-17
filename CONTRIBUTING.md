# Contributing to misch

Contributions should keep `misch` focused, dependency-light, tested, deterministic, and explicit about the limits of cppcheck's MISRA coverage.

## Development setup

```sh
uv sync
uv run misch --help
uv run pytest
uv run ruff check src tests
uv build
```

Unit tests do not require cppcheck. End-to-end engine coverage requires cppcheck and its bundled `misra.py` addon on `PATH`; tests that cannot find them are skipped.

## Architecture

A compilation database separates build-system concerns from analysis. cppcheck XML is parsed into a shared `Finding` model used by terminal, JSON, SARIF, and baseline reporting. Deviation auditing uses a separate model for suppressions and their justifications. See [Architecture](docs/architecture.md) for the pipeline, analysis boundary, and limitations.

## Repository layout

- `src/misch/cli.py`: command-line parsing and command handlers.
- `src/misch/config.py`: configuration loading, validation, and path resolution.
- `src/misch/db/`: compilation-database resolution, normalization, and scope classification.
- `src/misch/engine/cppcheck.py`: cppcheck invocation and XML parsing.
- `src/misch/report/`: finding, baseline, deviation, and rendering models.
- `src/misch/scaffold.py`: initialization planning and template loading.
- `src/misch/templates/`: packaged files emitted by scaffolded initialization.
- `tests/`: unit, behavior, and end-to-end tests.

## Contribution guidelines

- **Do not add MISRA guideline text.** Rule text is bring-your-own licensed material and must not be copied, paraphrased, or committed to this repository.
- **Support Python 3.11 and later.** Prefer the standard library; new runtime dependencies require a clear benefit.
- **Keep output deterministic.** Preserve stable ordering and serialization, and avoid timing-dependent tests.
- **State limitations plainly.** Do not present best-effort checks as certification or complete coverage.
- **Test behavior changes.** Add or update tests for every feature and bug fix.
- **Keep user documentation current.** Update the README or focused document whenever command or configuration behavior changes.

Run the full test, lint, and build commands before opening a pull request.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) such as `feat:`, `fix:`, `docs:`, `test:`, `chore:`, and `refactor:`. Commit messages should explain the reason for a change when it is not evident from the diff.
