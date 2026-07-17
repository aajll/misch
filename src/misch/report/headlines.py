"""Parse a bring-your-own MISRA headlines (rule-texts) file.

The harness ships no MISRA material. The user supplies the cppcheck-format
headlines file (see the rule-text documentation); we parse it only to attach a short
title and the guideline category (Mandatory / Required / Advisory) to findings.
Parsing is deliberately tolerant: anything we cannot read stays `unknown`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Category

# Matches "Rule 11.4", "Dir 4.9", "Directive 1.1" at the start of a rule block.
_RULE_HEAD = re.compile(
    r"^\s*(?P<kind>Rule|Dir|Directive)\s+(?P<maj>\d+)\.(?P<min>\d+)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_CATEGORY = re.compile(r"\b(Mandatory|Required|Advisory)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RuleInfo:
    category: Category
    headline: str


def load_headlines(path: str) -> dict[str, RuleInfo]:
    """Return {rule_id: RuleInfo}. Empty dict if the file is unreadable."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}

    out: dict[str, RuleInfo] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _RULE_HEAD.match(line)
        if not m:
            continue
        kind = m.group("kind").lower()
        prefix = "dir-" if kind.startswith("dir") else ""
        rule_id = f"misra-c2012-{prefix}{m.group('maj')}.{m.group('min')}"

        rest = (m.group("rest") or "").strip()
        cat = _CATEGORY.search(rest)
        category = _to_category(cat.group(1)) if cat else Category.UNKNOWN

        # The title is whatever text is left on the head line after removing the
        # category token; if that is empty (common: "Dir 1.1<tab>Required"), the
        # title is on the following non-empty, non-comment line.
        headline = _CATEGORY.sub("", rest).strip(" -\t")
        if not headline:
            headline = _next_text_line(lines, i + 1)

        out[rule_id.lower()] = RuleInfo(category, headline)
    return out


def _next_text_line(lines: list[str], start: int) -> str:
    """First non-empty, non-comment, non-rule-head line at or after `start`."""
    for j in range(start, min(start + 3, len(lines))):
        s = lines[j].strip()
        if s and not s.startswith("#") and not _RULE_HEAD.match(s):
            return s
    return ""


def _to_category(token: str) -> Category:
    return {
        "mandatory": Category.MANDATORY,
        "required": Category.REQUIRED,
        "advisory": Category.ADVISORY,
    }.get(token.lower(), Category.UNKNOWN)
