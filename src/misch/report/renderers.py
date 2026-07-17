"""Render normalised findings as terminal, JSON, and SARIF output."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..db import ScopeCoverage
from .baseline import BaselineDiff
from .model import Category, Finding, Report, Source

_CATEGORY_ORDER = {
    Category.MANDATORY: 0,
    Category.REQUIRED: 1,
    Category.ADVISORY: 2,
    Category.UNKNOWN: 3,
}
# Severity ramp with distinct hues, not just weight: mandatory gets a red
# block so it is unmistakable next to required (plain red).
_CATEGORY_STYLE = {
    Category.MANDATORY: "bold white on red",
    Category.REQUIRED: "bright_red",
    Category.ADVISORY: "yellow",
    Category.UNKNOWN: "dim",
}


def render_terminal(
    report: Report, coverage: ScopeCoverage, *, verbose: bool = False
) -> None:
    # emoji=False so a `:100:` line number is not read as an emoji shortcode;
    # highlight=False so rich does not restyle interpolated text. Dynamic data
    # (paths, messages) is still escaped before it reaches the markup parser.
    console = Console(stderr=False, emoji=False, highlight=False)
    misra = report.misra()

    console.rule("[bold]MISRA analysis")
    console.print(
        f"scope: [green]{len(coverage.analysed)}[/] analysed  "
        f"[dim]{len(coverage.excluded)} excluded[/]  "
        f"files with findings: {len({f.file for f in misra})}"
    )

    if not misra:
        console.print("\n[bold green]No MISRA findings.[/] [green]OK[/]")
        _maybe_cppcheck_note(console, report)
        return

    # Per-rule summary, most-severe category first, then by count.
    by_rule: dict[str, list[Finding]] = {}
    for f in misra:
        by_rule.setdefault(f.rule_id, []).append(f)

    table = Table(title="\nFindings by rule", title_justify="left")
    table.add_column("Rule", style="cyan", no_wrap=True)
    table.add_column("Category")
    table.add_column("Count", justify="right")
    table.add_column("Headline", overflow="fold")
    for rule_id, group in sorted(
        by_rule.items(),
        key=lambda kv: (_CATEGORY_ORDER[kv[1][0].category], -len(kv[1]), kv[0]),
    ):
        cat = group[0].category
        table.add_row(
            rule_id,
            f"[{_CATEGORY_STYLE[cat]}]{cat.value}[/]",
            str(len(group)),
            escape(group[0].headline or ""),
        )
    console.print(table)

    if verbose:
        # Detailed, deterministic listing.
        console.print("\n[bold]Locations[/]")
        for f in sorted(misra, key=Finding.sort_key):
            style = _CATEGORY_STYLE[f.category]
            loc = escape(f"{f.file}:{f.line}:{f.column}")
            console.print(
                f"  [dim]{loc}[/]  [{style}]{f.rule_id}[/]  {escape(f.message)}"
            )
    else:
        console.print("\n[dim]Run with -v/--verbose for the per-location listing.[/]")

    _print_summary(console, misra)
    _maybe_cppcheck_note(console, report)


def _print_summary(console: Console, misra: list[Finding]) -> None:
    counts = Counter(f.category for f in misra)
    parts = []
    order = (Category.MANDATORY, Category.REQUIRED, Category.ADVISORY, Category.UNKNOWN)
    for cat in order:
        if counts.get(cat):
            parts.append(f"[{_CATEGORY_STYLE[cat]}]{counts[cat]} {cat.value}[/]")
    console.print(f"\n[bold]{len(misra)} MISRA finding(s):[/] " + "  ".join(parts))


def _maybe_cppcheck_note(console: Console, report: Report) -> None:
    n = sum(1 for f in report.findings if f.source is Source.CPPCHECK)
    if n:
        console.print(
            f"[dim]({n} non-MISRA cppcheck diagnostic(s), often parse/config "
            f"issues worth fixing; see the JSON output.)[/]"
        )


def render_baseline_summary(d: BaselineDiff, path: Path) -> None:
    console = Console(stderr=False, emoji=False, highlight=False)
    console.rule("[bold]Baseline (ratchet)")
    console.print(
        f"[dim]{escape(path.name)}[/]: "
        f"[{'bold red' if d.new else 'green'}]{len(d.new)} new[/]  "
        f"[dim]{len(d.baselined)} baselined[/]  "
        f"[cyan]{len(d.fixed)} fixed[/]"
    )
    if d.new:
        console.print("\n[bold red]New findings (not in baseline):[/]")
        for f in sorted(d.new, key=Finding.sort_key):
            loc = escape(f"{f.file}:{f.line}")
            console.print(f"  [red]+[/] {loc}  {f.rule_id}  {escape(f.message)}")
    if d.fixed:
        console.print(
            f"\n[cyan]{len(d.fixed)} baselined finding(s) no longer occur[/]; "
            "re-run [bold]misch baseline[/] to prune them."
        )
    if not d.new:
        console.print("[green]No new findings.[/] [green]OK[/]")


def render_json(report: Report, coverage: ScopeCoverage, path: Path) -> None:
    doc = {
        "schema": "misch/findings@1",
        "summary": {
            "misra_findings": len(report.misra()),
            "total_findings": len(report.findings),
            "analysed_files": len(coverage.analysed),
            "excluded_files": len(coverage.excluded),
        },
        "findings": [
            {
                "rule_id": f.rule_id,
                "category": f.category.value,
                "source": f.source.value,
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "column": f.column,
                "message": f.message,
                "headline": f.headline,
                "fingerprint": f.fingerprint(),
            }
            for f in report.sorted()
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")


# SARIF severity level per MISRA category (advisory/unknown are warnings).
_SARIF_LEVEL = {
    Category.MANDATORY: "error",
    Category.REQUIRED: "error",
    Category.ADVISORY: "warning",
    Category.UNKNOWN: "warning",
}


def render_sarif(report: Report, path: Path) -> None:
    """Emit SARIF 2.1.0 for GitHub code scanning / IDE annotations."""
    findings = report.sorted()

    rules: dict[str, dict] = {}
    for f in findings:
        if f.rule_id in rules:
            continue
        rule: dict = {"id": f.rule_id, "properties": {"category": f.category.value}}
        if f.headline:
            rule["name"] = f.rule_id
            rule["shortDescription"] = {"text": f.headline}
        rules[f.rule_id] = rule

    results = []
    for f in findings:
        region = {"startLine": max(f.line, 1)}
        if f.column > 0:
            region["startColumn"] = f.column
        results.append(
            {
                "ruleId": f.rule_id,
                "level": _SARIF_LEVEL[f.category] if f.is_misra else "note",
                "message": {"text": f.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f.file},
                            "region": region,
                        }
                    }
                ],
            }
        )

    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "misch",
                        "informationUri": "https://github.com/aajll/misch",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
