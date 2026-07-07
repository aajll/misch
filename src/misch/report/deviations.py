"""Harvest and validate MISRA deviations.

Every `cppcheck-suppress*` in the code and every entry in a project
suppressions file is a MISRA deviation that must carry a rationale and land in
the deviation record. This module collects them (grammar-aware), enforces a
justification, validates the rule id against the known rule set, and renders an
audit-ready record.

Staleness (a suppression that no longer matches any finding) is checked by
`find_stale`: an unsuppressed engine run reveals what each suppression hides,
and any inline suppression whose rule has no finding at its site is dead. This
sidesteps cppcheck's `unmatchedSuppression`, which it does not reliably emit
under `--project`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import is_misra_id

# One line may carry any of the cppcheck inline-suppression forms. `ids` is the
# bracketed multi-rule form; `id` the single-rule form. `tail` is everything
# after, from which we extract the justification.
_SUPPRESS = re.compile(
    r"cppcheck-suppress"
    r"(?P<kind>-begin|-end|-file|-macro)?"
    r"\s*(?:\[(?P<ids>[^\]]+)\]|\s(?P<id>[A-Za-z0-9_.\-]+))"
    r"(?P<tail>[^\n]*)"
)
# Our justification convention: text after ';' (optionally tagged @deviation).
_JUSTIFY_TAG = re.compile(r"^\s*(?:@deviation|@rationale|reason:)\s*", re.IGNORECASE)
_SOURCE_GLOB = ("*.c", "*.h", "*.cpp", "*.hpp", "*.cc")


@dataclass(slots=True)
class Deviation:
    origin: str  # "inline" | "project"
    kind: str  # suppress | suppress-begin/-end/-file/-macro | project
    rule_ids: list[str]
    file: str
    line: int | None
    justification: str
    unknown_ids: list[str] = field(default_factory=list)  # misra ids not in rules

    @property
    def justified(self) -> bool:
        return bool(self.justification.strip())

    @property
    def valid(self) -> bool:
        return self.justified and not self.unknown_ids


def _justification(tail: str) -> str:
    tail = tail.strip()
    if tail.endswith("*/"):
        tail = tail[:-2].strip()
    if ";" not in tail:
        return ""
    reason = tail.split(";", 1)[1].strip()
    return _JUSTIFY_TAG.sub("", reason).strip()


def _validate_ids(rule_ids: list[str], known: set[str]) -> list[str]:
    if not known:  # no headlines => cannot validate; do not false-flag
        return []
    return [r for r in rule_ids if is_misra_id(r) and r.lower() not in known]


def harvest_inline(files: list[Path], root: Path, known: set[str]) -> list[Deviation]:
    out: list[Deviation] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(root, path)
        lines = text.splitlines()
        n = len(lines)
        i = 0
        while i < n:
            m = _SUPPRESS.search(lines[i])
            if not m:
                i += 1
                continue
            if m.group("ids"):
                ids = [s.strip() for s in m.group("ids").split(",") if s.strip()]
            else:
                ids = [m.group("id")]
            kind = "suppress" + (m.group("kind") or "")
            tail = m.group("tail")
            anchor = i  # 0-based line the suppression is checked against
            # A cppcheck-suppress inside a block comment that does not close on
            # its own line continues onto following lines: they extend the
            # justification, and cppcheck binds the suppression to the code
            # after the comment closes. Accumulate the rationale and anchor
            # staleness at the closing line, not at the token line.
            if "/*" in lines[i] and "*/" not in tail:
                j = i + 1
                while j < n:
                    cont = lines[j].strip()
                    if cont.startswith("*") and not cont.startswith("*/"):
                        cont = cont[1:].strip()  # drop the leading comment star
                    tail += " " + cont
                    if "*/" in lines[j]:
                        break
                    j += 1
                anchor = min(j, n - 1)
                i = anchor
            out.append(
                Deviation(
                    origin="inline",
                    kind=kind,
                    rule_ids=ids,
                    file=rel,
                    line=anchor + 1,
                    justification=_justification(tail),
                    unknown_ids=_validate_ids(ids, known),
                )
            )
            i += 1
    return out


def parse_suppressions_file(path: Path, root: Path, known: set[str]) -> list[Deviation]:
    """Parse a cppcheck suppressions file; a preceding comment block is the
    justification for the entry that follows it."""
    out: list[Deviation] = []
    comment: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            comment = []
            continue
        if s.startswith("#"):
            comment.append(s.lstrip("# ").rstrip())
            continue
        # Entry form: id | id:file | id:file:line | *:glob
        parts = s.split(":")
        rule = parts[0]
        loc_file = parts[1] if len(parts) > 1 else ""
        loc_line = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        out.append(
            Deviation(
                origin="project",
                kind="project",
                rule_ids=[rule],
                file=loc_file,
                line=loc_line,
                justification=" ".join(comment).strip(),
                unknown_ids=_validate_ids([rule], known),
            )
        )
        comment = []
    return out


def discover_sources(cfg) -> list[Path]:
    """In-scope source files by walking the scope roots (no build required).

    Uses the same scope/exclude rules as analysis, so `deviations` can run as a
    fast pre-commit check without configuring a compile DB.
    """
    from ..db import in_scope

    exts = {".c", ".h", ".cpp", ".hpp", ".cc"}
    roots = cfg.scope or ["."]
    seen: set[Path] = set()
    out: list[Path] = []
    for entry in roots:
        base = cfg.project_root / entry.rstrip("/")
        candidates = base.rglob("*") if base.is_dir() else cfg.project_root.glob(entry)
        for p in candidates:
            if p.suffix not in exts or not p.is_file() or p in seen:
                continue
            rel = p.resolve().relative_to(cfg.project_root.resolve()).as_posix()
            if in_scope(cfg, rel):
                seen.add(p)
                out.append(p)
    return sorted(out)


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def find_stale(deviations: list[Deviation], findings: list) -> list[str]:
    """Return "file:line" for inline suppressions that hide nothing.

    Cross-references harvested inline `cppcheck-suppress` sites against findings
    from an *unsuppressed* run: a suppression at line L for rule R is live if an
    unsuppressed finding of R sits at line L or L+1 (a suppression comment
    applies to its own line or the next). Anything else is a dead deviation.
    File/begin/end/macro forms and project-level entries are not line-checkable
    here and are left alone.
    """
    by_file_rule: dict[tuple[str, str], set[int]] = {}
    for f in findings:
        by_file_rule.setdefault((f.file, f.rule_id.lower()), set()).add(f.line)

    stale: list[str] = []
    for d in deviations:
        if d.origin != "inline" or d.kind != "suppress" or d.line is None:
            continue
        live = any(
            (d.file, rid.lower()) in by_file_rule
            and by_file_rule[(d.file, rid.lower())] & {d.line, d.line + 1}
            for rid in d.rule_ids
        )
        if not live:
            stale.append(f"{d.file}:{d.line}")
    return stale


@dataclass(slots=True)
class DeviationRecord:
    deviations: list[Deviation] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)  # "file:line" of dead suppressions

    @property
    def unjustified(self) -> list[Deviation]:
        return [d for d in self.deviations if not d.justified]

    @property
    def unknown(self) -> list[Deviation]:
        return [d for d in self.deviations if d.unknown_ids]

    def ok(self) -> bool:
        return not self.unjustified and not self.unknown
