"""Baseline / ratchet: accept the current finding set, fail only on new ones.

This is how MISRA gets adopted on an existing codebase without a wall of
findings on day one. Findings are keyed by their line-independent fingerprint,
so edits above a finding do not churn the baseline.

The baseline stores an occurrence **count** per fingerprint, not just presence.
Two findings of the same rule in one file can share a fingerprint (a generic
message, no distinguishing symbol); counting means adding a second such finding
is still caught as new, while moving an existing one does not churn.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .model import Finding

SCHEMA = "misch/baseline@2"


@dataclass(slots=True)
class BaselineDiff:
    new: list[Finding] = field(default_factory=list)
    baselined: list[Finding] = field(default_factory=list)
    fixed: list[dict] = field(default_factory=list)  # {rule_id,file,message,count}


def _groups(findings: list[Finding]) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {}
    for f in findings:
        if f.is_misra:
            out.setdefault(f.fingerprint(), []).append(f)
    return out


def write_baseline(path: Path, findings: list[Finding]) -> int:
    """Snapshot MISRA findings as {fingerprint: {..., count}}. Returns total."""
    records: dict[str, dict] = {}
    total = 0
    for fp, group in _groups(findings).items():
        rep = sorted(group, key=Finding.sort_key)[0]
        records[fp] = {
            "rule_id": rep.rule_id,
            "file": rep.file,
            "message": rep.message,
            "count": len(group),
        }
        total += len(group)
    doc = {"schema": SCHEMA, "fingerprints": dict(sorted(records.items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return total


def load_baseline(path: Path) -> dict[str, dict]:
    """Return {fingerprint: record}. Empty if the file is absent."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text()).get("fingerprints", {})


def diff(findings: list[Finding], baseline: dict[str, dict]) -> BaselineDiff:
    out = BaselineDiff()
    groups = _groups(findings)
    base_counts = Counter({fp: rec.get("count", 1) for fp, rec in baseline.items()})

    for fp, group in groups.items():
        ordered = sorted(group, key=Finding.sort_key)
        base = base_counts.get(fp, 0)
        # The first `base` occurrences are covered; any excess is new.
        out.baselined.extend(ordered[:base])
        out.new.extend(ordered[base:])

    for fp, rec in baseline.items():
        remaining = rec.get("count", 1) - len(groups.get(fp, []))
        if remaining > 0:
            out.fixed.append({**rec, "count": remaining})
    return out
