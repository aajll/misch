"""Generate `misra.toml` and optional analysis-tree templates.

`misch init` renders a documented, valid config. Scaffold mode also plans a
conventional `analysis/` tree, but every target is checked before anything is
written so initialization cannot partially overwrite an existing setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files as resource_files
from pathlib import Path


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
    scaffolded: bool = False


class ScaffoldConflict(Exception):
    """One or more initialization targets already exist."""

    def __init__(self, paths: list[Path]):
        self.paths = paths
        super().__init__(", ".join(str(path) for path in paths))


class ScaffoldPathError(Exception):
    """A generated target cannot be represented by regular files."""

    def __init__(self, paths: list[Path]):
        self.paths = paths
        super().__init__(", ".join(str(path) for path in paths))


def _toml_array(items: list[str]) -> str:
    if not items:
        return "[]"
    inner = ", ".join(f'"{i}"' for i in items)
    return f"[{inner}]"


def build_config(p: ScaffoldParams) -> str:
    """Render the template TOML as text (comments preserved; not via a writer)."""
    lines: list[str] = []
    add = lines.append

    add("# misch configuration. See the project configuration reference:")
    add("# https://github.com/aajll/misch/blob/master/docs/configuration.md")
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
        add('# path = "build/compile_commands.json"   # source = "existing" only')
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
    if p.scaffolded:
        add("# Bring your own licensed rule texts; see analysis/rules/README.md.")
    else:
        add("# Bring your own licensed MISRA headlines. Format and handling guide:")
        add("# https://github.com/aajll/misch/blob/master/docs/rule-texts.md")
    add("# Precedence: $MISRA_RULE_TEXTS > this value. Absent => category: unknown.")
    add(f'texts = "{p.rule_texts}"')
    add("")

    add("[report]")
    add("# Default output is the terminal. Add file outputs as needed, e.g.:")
    add('#   outputs = ["terminal", {format = "json", path = "misra.json"}]')
    add('outputs = ["terminal"]')
    add("")

    add("[deviations]")
    if p.scaffolded:
        add("# Project-level deviations; prefer justified inline suppressions.")
        add('suppressions = "analysis/deviations/misra-deviations.txt"')
    else:
        add("# Optional project-level (blanket) MISRA deviations: a cppcheck")
        add("# suppressions file whose entries are applied during analysis and")
        add("# harvested into the deviation record by `misch deviations`. Each")
        add("# entry's preceding comment block is its mandatory justification. A")
        add("# bare rule id deviates that rule everywhere; add :file or :file:line")
        add("# to narrow the scope. Prefer an inline cppcheck-suppress for a single")
        add("# justified site.")
        add('# suppressions = "misra-deviations.txt"')
    add("")

    if p.scaffolded:
        add("[baseline]")
        add("# Created only when `misch baseline` explicitly accepts findings.")
        add('path = "analysis/baseline/misra-baseline.json"')
        add("")

    add("# Multi-platform profiles. Use `misch run --platform <name>` to select.")
    add("# Scalar values are replaced; lists are replaced by default or extended")
    add("# with the append_ prefix (e.g. toolchain.append_defines).")
    add("# [profiles.aarch64]")
    add('# platform.preset = "arm"')
    add('# toolchain.append_defines = ["ARCH_ARM64"]')
    add("")

    return "\n".join(lines)


def build_project_files(out: Path, p: ScaffoldParams) -> dict[Path, str]:
    """Return every file `init` would write, without touching the filesystem."""
    files = {out: build_config(p)}
    if not p.scaffolded:
        return files

    root = out.parent / "analysis"
    for relative in _SCAFFOLD_TEMPLATES:
        template = resource_files("misch").joinpath(
            "templates", "analysis", *relative.parts
        )
        files[root / relative] = template.read_text(encoding="utf-8")
    return files


def write_project_files(
    out: Path, p: ScaffoldParams, *, force: bool = False
) -> list[Path]:
    """Write an init plan after preflighting all targets.

    Without ``force``, any existing target aborts the operation before a file
    or directory is created. With ``force``, every generated target is replaced.
    """
    files = build_project_files(out, p)
    root = out.parent
    invalid = sorted(
        {
            blocker
            for path in files
            if (blocker := _path_blocker(path, root)) is not None
        }
    )
    if invalid:
        raise ScaffoldPathError(invalid)

    conflicts = sorted(path for path in files if path.exists())
    if conflicts and not force:
        raise ScaffoldConflict(conflicts)

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)


def _path_blocker(path: Path, root: Path) -> Path | None:
    """Return an invalid target or parent inside the generated layout."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return path

    parent = path.parent
    while parent != root:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return parent
        parent = parent.parent

    anchor = root
    while not anchor.exists() and anchor != anchor.parent:
        if anchor.is_symlink():
            return anchor
        anchor = anchor.parent
    if anchor.is_symlink() or (anchor.exists() and not anchor.is_dir()):
        return anchor
    return None


_SCAFFOLD_TEMPLATES = (
    Path("README.md"),
    Path("rules/README.md"),
    Path("deviations/misra-deviations.txt"),
    Path("baseline/README.md"),
)
