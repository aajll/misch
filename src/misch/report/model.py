"""Normalised findings used by analysis reports and baseline comparison."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum

# The cppcheck misra addon emits rule ids in the historical `misra-c2012-X.Y`
# form even for the C:2023 revision (same rule numbering).
_MISRA_ID = re.compile(r"^misra-c20\d\d-(?:dir-)?\d+\.\d+$", re.IGNORECASE)


class Category(StrEnum):
    """MISRA guideline category, parsed from the BYO headlines file."""

    MANDATORY = "mandatory"
    REQUIRED = "required"
    ADVISORY = "advisory"
    UNKNOWN = "unknown"


class Source(StrEnum):
    """Which checker produced the finding."""

    MISRA = "misra"  # the misra.py addon
    CPPCHECK = "cppcheck"  # cppcheck's own checks (parse errors, etc.)


@dataclass(frozen=True, slots=True)
class Finding:
    """One normalised diagnostic at one source location."""

    rule_id: str  # e.g. "misra-c2012-11.4" or "nullPointer"
    message: str
    file: str  # project-relative POSIX path
    line: int
    column: int = 0
    severity: str = "style"  # cppcheck severity verbatim
    category: Category = Category.UNKNOWN
    source: Source = Source.MISRA
    headline: str = ""  # short rule title from the headlines file
    symbol: str = ""

    @property
    def is_misra(self) -> bool:
        return self.source is Source.MISRA

    def fingerprint(self) -> str:
        """Stable identity for baselining.

        Deliberately excludes the line number so a finding survives edits above
        it; keyed on rule + file + symbol + a normalised message (literals
        stripped). Including the symbol distinguishes two same-rule findings in
        one file (which would otherwise share a generic message) without
        reintroducing line churn, since symbol names are edit-stable.
        """
        norm = _normalise_message(self.message)
        raw = f"{self.rule_id}\0{self.file}\0{self.symbol}\0{norm}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def sort_key(self) -> tuple:
        return (self.rule_id, self.file, self.line, self.column)


def is_misra_id(rule_id: str) -> bool:
    return bool(_MISRA_ID.match(rule_id))


_NUM = re.compile(r"\b\d+\b")
_QUOTED = re.compile(r"'[^']*'")


def _normalise_message(msg: str) -> str:
    """Blank out volatile tokens (numbers, 'quoted symbols') for a stable hash."""
    msg = _QUOTED.sub("'X'", msg)
    msg = _NUM.sub("N", msg)
    return " ".join(msg.split())


@dataclass(slots=True)
class Report:
    """A full analysis result: findings plus scope coverage."""

    findings: list[Finding] = field(default_factory=list)
    analysed_files: list[str] = field(default_factory=list)
    excluded_files: list[str] = field(default_factory=list)

    def misra(self) -> list[Finding]:
        return [f for f in self.findings if f.is_misra]

    def sorted(self) -> list[Finding]:
        return sorted(self.findings, key=Finding.sort_key)
