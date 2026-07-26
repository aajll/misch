"""Execute analysis-driven CLI commands behind one orchestration seam."""

from __future__ import annotations

import argparse
from collections import Counter

from rich.console import Console

from .analysis import (
    AnalysisResult,
    AnalysisSuccess,
    DbAdapterImpl,
    DbFailure,
    EngineAdapterImpl,
    EngineFailure,
    ScopeFailure,
    analyse,
)
from .config import Config, ConfigError, load
from .report.baseline import diff as baseline_diff
from .report.baseline import load_baseline, write_baseline
from .report.dev_render import render_markdown as render_dev_markdown
from .report.dev_render import render_terminal as render_dev_terminal
from .report.deviations import harvest
from .report.headlines import load_headlines
from .report.renderers import render

_err = Console(stderr=True)


def run(args: argparse.Namespace) -> int:
    """Run an analysis-driven command and return its process exit code."""
    cfg = _load_config(args)
    if cfg is None:
        return 2

    if args.cmd == "deviations":
        return _handle_deviations(args, cfg)

    success = _run_analysis(cfg)
    if success is None:
        return 2
    if args.cmd == "run":
        return _handle_run(args, cfg, success)
    if args.cmd == "baseline":
        return _handle_baseline(args, cfg, success)

    _err.print(f"[red]unsupported execution command:[/] {args.cmd}")
    return 2


def _load_config(args: argparse.Namespace) -> Config | None:
    try:
        return load(args.config, profile_name=args.profile)
    except ConfigError as exc:
        _err.print(f"[red]config error:[/] {exc}")
        return None


def _run_analysis(cfg: Config, *, inline_suppr: bool = True) -> AnalysisSuccess | None:
    with _err.status("Running analysis..."):
        result = analyse(
            cfg, DbAdapterImpl(), EngineAdapterImpl(), inline_suppr=inline_suppr
        )
    return _dispatch_analysis_result(result)


def _dispatch_analysis_result(
    result: AnalysisResult, *, report_errors: bool = True
) -> AnalysisSuccess | None:
    """Return a success result or handle an analysis failure in one place."""
    if isinstance(result, AnalysisSuccess):
        return result
    if not report_errors:
        return None
    if isinstance(result, DbFailure):
        _err.print(f"[red]compile-db error:[/] {result.message}")
    elif isinstance(result, ScopeFailure):
        _print_scope_error(result)
    elif isinstance(result, EngineFailure):
        _err.print(f"[red]engine error:[/] {result.message}")
    else:
        _err.print("[red]unexpected analysis result[/]")
    return None


def _print_scope_error(failure: ScopeFailure) -> None:
    if "finding(s)" in failure.message:
        by_file = Counter(failure.unattributed)
        _err.print(
            f"[red]scope error:[/] {len(failure.unattributed)} finding(s) at "
            "location(s) matching neither \\[project].scope nor "
            "\\[project].exclude. Classify them explicitly "
            "(audit boundary must be enumerated):"
        )
        for file, count in sorted(by_file.items()):
            _err.print(f"  [red]?[/] {file} ({count})")
        return
    _err.print(
        f"[red]scope error:[/] {len(failure.unattributed)} file(s) match "
        "neither \\[project].scope nor \\[project].exclude. Classify them "
        "explicitly (audit boundary must be enumerated):"
    )
    for file in failure.unattributed:
        _err.print(f"  [red]?[/] {file}")


def _handle_run(args: argparse.Namespace, cfg: Config, success: AnalysisSuccess) -> int:
    report, coverage = success.report, success.coverage
    diff = None
    if args.baseline:
        baseline = load_baseline(cfg.baseline_path)
        if not baseline:
            _err.print(
                f"[yellow]note:[/] --baseline set but {cfg.baseline_path.name} is "
                "empty/absent. Run [bold]misch baseline[/] to create it."
            )
        diff = baseline_diff(report.misra(), baseline)

    render(
        report,
        coverage,
        _outputs(args, cfg),
        verbose=getattr(args, "verbose", False),
        diff=diff,
    )
    if not args.baseline:
        return 1 if report.misra() else 0
    return 1 if diff.new else 0


def _outputs(args: argparse.Namespace, cfg: Config) -> list[dict]:
    if not args.format:
        return list(cfg.outputs)

    outputs = []
    for fmt in args.format:
        entry: dict = {"format": fmt}
        if fmt in ("json", "sarif"):
            default_name = "misra.json" if fmt == "json" else "misra.sarif"
            entry["path"] = str(args.output or cfg.build_dir / default_name)
        outputs.append(entry)
    return outputs


def _handle_baseline(
    args: argparse.Namespace, cfg: Config, success: AnalysisSuccess
) -> int:
    path = args.baseline_file or cfg.baseline_path
    count = write_baseline(path, success.report.misra())
    _err.print(
        f"[green]wrote baseline[/] {path}: {count} finding(s) accepted. "
        "Future [bold]misch run --baseline[/] fails only on new ones."
    )
    return 0


def _handle_deviations(args: argparse.Namespace, cfg: Config) -> int:
    known = set(load_headlines(cfg.rule_texts)) if cfg.rule_texts else set()
    findings = None
    if args.check_stale:
        with _err.status("Running analysis..."):
            result = analyse(
                cfg, DbAdapterImpl(), EngineAdapterImpl(), inline_suppr=False
            )
        success = _dispatch_analysis_result(result, report_errors=False)
        if success is None:
            _err.print("[yellow]note:[/] staleness check skipped (analysis failed).")
        else:
            findings = success.report.findings

    record = harvest(cfg, known, findings=findings)
    render_dev_terminal(record)
    for fmt in args.format:
        if fmt == "md":
            path = args.output or (cfg.build_dir / "deviations.md")
            render_dev_markdown(record, path)
            _err.print(f"[dim]wrote deviation record to {path}[/]")
        else:
            _err.print(f"[yellow]note:[/] deviations format {fmt!r} unsupported")
    return 0 if record.ok() else 1
