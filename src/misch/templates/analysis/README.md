# MISRA analysis assets

This directory was created by `misch init --scaffold`. Its configured paths are resolved relative to the project-root `misra.toml`.

- `rules/` explains how to supply licensed MISRA rule headlines.
- `deviations/` contains reviewed project-level cppcheck suppressions.
- `baseline/` is the configured destination for accepted finding counts.

## Recommended workflow

1. Review `misra.toml`, especially the scope, exclusions, and compilation-database source.
2. Supply rule texts as described in `rules/README.md` when headlines and categories are needed.
3. Run `misch run`, then fix findings or document justified deviations.
4. Run `misch baseline` only after explicitly accepting the current finding counts.
5. Commit reviewed project deviations and the generated baseline so CI can enforce them.

Do not commit MISRA guideline text unless its licence and the repository's access controls permit that distribution.
