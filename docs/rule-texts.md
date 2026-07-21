# Rule texts

`misch` and cppcheck do not need guideline prose to detect findings. An optional rule-text file adds human-readable headlines and Mandatory, Required, or Advisory categories to reports.

MISRA C:2023 guideline text is copyrighted by The MISRA Consortium. `misch` does not bundle, reproduce, derive, or generate it. Create any rule-text file from a copy you are licensed to use and store or distribute it only as that licence permits. The MISRA Consortium does provide some base headlines [here](https://gitlab.com/MISRA/MISRA-C/MISRA-C-2012/tools) to get you started.

## Configure the file

The file must use the format accepted by cppcheck's `misra.py` addon. Point `misch` at it with an environment variable:

```sh
export MISRA_RULE_TEXTS=/secure/path/misra-headlines.txt
```

Or set a path in `misra.toml`:

```toml
[rules]
texts = "/secure/path/misra-headlines.txt"
```

`MISRA_RULE_TEXTS` is checked first. If it does not identify a readable file, `misch` checks `[rules].texts`. Environment variables in the configured value are expanded, and a relative configured path is resolved from the directory containing `misra.toml`.

Without a readable rule-text file, analysis continues and reports each category as `unknown`.

## Required format

The file must include a line containing both `Appendix A` and `Summary of guidelines`, followed by rule or directive entries in cppcheck's expected form:

```text
Appendix A Summary of guidelines
Rule <chapter>.<rule> <Mandatory|Required|Advisory>
<licensed headline>
```

For example, the entry header uses a shape such as `Rule 11.4 Advisory`; directives use `Dir` instead of `Rule`. The category may appear on the entry header or the following line.

The appendix heading is required by cppcheck's parser. If it is absent, cppcheck may report findings without loading the supplied text. `misch` parses the same file on a best-effort basis; an unreadable entry remains in the `unknown` category rather than failing analysis.

See cppcheck's [MISRA addon documentation](https://cppcheck.sourceforge.io/misra.php) for its current file-format requirements.

## Use in CI

Provide the file from an access-controlled location and set `MISRA_RULE_TEXTS` before invoking `misch`. Do not place licensed text in a public repository. Whether it may be stored in a private repository or secret store depends on the applicable licence and access controls.

The conventional local filename `misra_c_2023_headlines_for_cppcheck.txt` is excluded by this repository's `.gitignore`, but consumers should add an appropriate exclusion to their own project when needed.
