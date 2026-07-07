"""Generate a commented `misra.toml` template.

`misch init` renders this so a new project starts from a documented,
valid config instead of a blank file. Flags pre-fill each section; with no
flags the template carries sensible defaults and placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScaffoldParams:
    scope: list[str] = field(default_factory=lambda: ["src/"])
    exclude: list[str] = field(default_factory=lambda: ["tests/", "subprojects/"])
    db_source: str = "meson"
    db_path: str | None = None
    platform_preset: str = "unix64"
    platform_xml: str | None = None
    rule_texts: str = "${MISRA_RULE_TEXTS}"
    defines: list[str] = field(default_factory=list)


def _toml_array(items: list[str]) -> str:
    if not items:
        return "[]"
    inner = ", ".join(f'"{i}"' for i in items)
    return f"[{inner}]"


def build_config(p: ScaffoldParams) -> str:
    """Render the template TOML as text (comments preserved; not via a writer)."""
    lines: list[str] = []
    add = lines.append

    add("# misch configuration. See misch/docs/DESIGN.md for the full schema.")
    add("")

    add("[project]")
    add("# Analysed source roots. Directory-style entries match everything beneath")
    add('# them; globs (e.g. "*.c") are also honoured.')
    add(f"scope   = {_toml_array(p.scope)}")
    add("# Explicitly excluded. Every compile-DB file must match scope OR exclude;")
    add("# an unclassified file is a hard error (no silent scope creep).")
    add(f"exclude = {_toml_array(p.exclude)}")
    add("")

    add("[db]")
    add("# Where compile_commands.json comes from: meson | cmake | existing.")
    add('# Plain-Make projects: generate one (e.g. `bear -- make`) and use "existing".')
    add(f'source = "{p.db_source}"')
    if p.db_source == "existing":
        path = p.db_path or "build/compile_commands.json"
        add(f'path   = "{path}"')
    else:
        add('# path = "build/compile_commands.json"   # only for source = "existing"')
    add("")

    add("[platform]")
    if p.platform_xml:
        add("# Custom cppcheck platform description (e.g. a 16-bit-MAU target).")
        add(f'xml = "{p.platform_xml}"')
    else:
        add("# A cppcheck built-in (unix64, unix32, ...) or set [platform].xml to a")
        add("# custom platform description for exotic targets.")
        add(f'preset = "{p.platform_preset}"')
    add("")

    add("[toolchain]")
    add("# Extra -D defines cppcheck should see (e.g. neutralising compiler")
    add('# keywords on a cross target: "__interrupt=").')
    add(f"defines = {_toml_array(p.defines)}")
    add("")

    add("[rules]")
    add("# Bring-your-own MISRA headlines (see docs/rule-texts.md). Never commit it.")
    add("# Precedence: $MISRA_RULE_TEXTS > this value. Absent => category: unknown.")
    add(f'texts = "{p.rule_texts}"')
    add("")

    add("[report]")
    add("# Default output is the terminal. Add file outputs as needed, e.g.:")
    add('#   outputs = ["terminal", {format = "json", path = "misra.json"}]')
    add('outputs = ["terminal"]')
    add("")

    add("[deviations]")
    add("# Optional project-level (blanket) MISRA deviations: a cppcheck")
    add("# suppressions file whose entries are applied during analysis and")
    add("# harvested into the deviation record by `misch deviations`. Each")
    add("# entry's preceding comment block is its mandatory justification. A")
    add("# bare rule id deviates that rule everywhere; add :file or :file:line")
    add("# to narrow the scope. Prefer an inline cppcheck-suppress for a single")
    add("# justified site.")
    add('# suppressions = "misra-deviations.txt"')
    add("")

    return "\n".join(lines)
