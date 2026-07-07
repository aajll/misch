# Bring-your-own MISRA rule texts (headlines)

`misch` ships **no MISRA material**. The MISRA C:2023 guideline text is copyrighted by The MISRA Consortium and must not be redistributed, so the harness never bundles, reproduces, or derives it.

## What the file is for

cppcheck's `misra.py` addon runs its **checks** without any rule-texts file; it just reports rule numbers (`misra-c2012-11.4`). The rule-texts file adds two things the harness uses for display and classification only:

- a short **headline** per rule, and
- the guideline **category** (Mandatory / Required / Advisory).

Without it, analysis still runs; findings are simply tagged `category: unknown`.

## How to provide it

Create the file from your own licensed copy of the MISRA C:2023 document, in the [cppcheck rule-texts format](https://cppcheck.sourceforge.io/misra.php), then point the harness at it by either exporting an environment variable:

```sh
export MISRA_RULE_TEXTS=/path/to/misra_c_2023_headlines_for_cppcheck.txt
```

or naming it in `misra.toml`:

```toml
[rules]
texts = "${MISRA_RULE_TEXTS}"          # env-expanded
# texts = "analysis/headlines.txt"     # or a path relative to the config
```

Resolution precedence is `$MISRA_RULE_TEXTS` → `[rules].texts`.

## In CI

Inject the file as a secret (or fetch it from a private, licensed location) and export `MISRA_RULE_TEXTS` before `misch run`. It must never be committed to a public repository; `.gitignore` already excludes the conventional filename.

## Format the parser expects

Tolerant and best-effort. Each rule block begins with a line like:

```
Appendix A Summary of guidelines
Rule 11.4 Advisory
A conversion should not be performed between a pointer to object and an integer type
```

or `Dir 4.9 Advisory` for directives. The category token (`Mandatory` / `Required` / `Advisory`) may sit on the head line or the following line. Anything the parser cannot read stays `category: unknown`, and never fails the run.

The `Appendix A Summary of guidelines` first line matters: `misch` passes the same file to cppcheck's `misra.py` addon, whose own parser skips everything **until** a line containing `Appendix A` and `Summary of guidelines`. Without that heading the addon loads zero rules and tags every finding with a misleading `(rule-texts-file not found: ...)` note, even though `misch`'s table still shows headlines and categories (its parser is more tolerant). Files exported for cppcheck usually carry the heading already.
