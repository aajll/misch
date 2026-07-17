# MISRA rule texts

`misch` does not ship MISRA guideline text. To add headlines and categories to reports, create a cppcheck-format file from a copy you are licensed to use and set `[rules].texts` in `misra.toml` or the `MISRA_RULE_TEXTS` environment variable. The environment variable is checked first.

The file must contain an `Appendix A Summary of guidelines` heading followed by rule or directive entries in cppcheck's expected format. See the [misch rule-text guide](https://github.com/aajll/misch/blob/master/docs/rule-texts.md) for format and CI guidance.

Do not place licensed text in a public repository. Whether it may be stored in a private repository or secret store depends on the applicable licence and access controls.
