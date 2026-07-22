"""`misch` command-line entry point: run, init, baseline, deviations."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from functools import partial
from pathlib import Path

from rich.console import Console
from rich_argparse import RichHelpFormatter

from . import __version__
from . import db as dbmod
from .config import ConfigError, load
from .engine import cppcheck
from .engine.cppcheck import EngineError
from .report.baseline import diff as bl_diff
from .report.baseline import load_baseline, write_baseline
from .report.dev_render import render_markdown as render_dev_markdown
from .report.dev_render import render_terminal as render_dev_terminal
from .report.deviations import (
    DeviationRecord,
    discover_sources,
    find_stale,
    harvest_inline,
    parse_suppressions_file,
)
from .report.headlines import load_headlines
from .report.model import Report
from .report.renderers import (
    render_baseline_summary,
    render_json,
    render_sarif,
    render_terminal,
)
from .scaffold import (
    ScaffoldConflict,
    ScaffoldParams,
    ScaffoldPathError,
    write_project_files,
)

_err = Console(stderr=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="misch",
        description="Config-driven MISRA C:2023 analysis for arbitrary C projects.",
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_parser = partial(sub.add_parser, formatter_class=RichHelpFormatter)

    p_run = add_parser("run", help="analyse a project")
    p_run.add_argument("-c", "--config", default="misra.toml", type=Path)
    p_run.add_argument(
        "--format",
        action="append",
        default=[],
        help="override outputs, e.g. --format json (repeatable)",
    )
    p_run.add_argument("--output", type=Path, help="path for a file format")
    p_run.add_argument(
        "--baseline",
        action="store_true",
        help="ratchet mode: report all, but fail only on findings not in the baseline",
    )
    p_run.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the per-location listing (default: table + summary)",
    )
    p_run.add_argument("--platform", help="platform profile name")
    p_run.set_defaults(func=_cmd_run)

    _add_init_parser(add_parser)

    p_bl = add_parser("baseline", help="snapshot current findings as the baseline")
    p_bl.add_argument("-c", "--config", default="misra.toml", type=Path)
    p_bl.add_argument("--baseline-file", type=Path, help="override the baseline path")
    p_bl.add_argument("--platform", help="platform profile name")
    p_bl.set_defaults(func=_cmd_baseline)

    p_dev = add_parser(
        "deviations", help="harvest + validate MISRA deviations (suppressions)"
    )
    p_dev.add_argument("-c", "--config", default="misra.toml", type=Path)
    p_dev.add_argument("--platform", help="platform profile name")
    p_dev.add_argument(
        "--check-stale",
        action="store_true",
        help="run the engine to flag suppressions that match no finding",
    )
    p_dev.add_argument(
        "--format", action="append", default=[], help="e.g. --format md (repeatable)"
    )
    p_dev.add_argument("--output", type=Path, help="path for the Markdown record")
    p_dev.set_defaults(func=_cmd_deviations)

    args = parser.parse_args(argv)
    return args.func(args)


def _add_init_parser(add_parser) -> None:
    p = add_parser("init", help="generate a misra.toml template")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("misra.toml"),
        help="where to write (default: ./misra.toml)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite every generated file that already exists",
    )
    p.add_argument(
        "--scaffold",
        action="store_true",
        help="also create a documented analysis/ directory tree",
    )
    p.add_argument(
        "--db",
        choices=["meson", "cmake", "existing"],
        default="meson",
        help="compile-DB source (default: meson)",
    )
    p.add_argument("--db-path", help="compile_commands.json path (for --db existing)")
    p.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="GLOB",
        help="analysed source root (repeatable; default: src/)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="excluded path (repeatable; default: tests/, subprojects/)",
    )
    p.add_argument("--platform", default="unix64", help="cppcheck platform preset")
    p.add_argument(
        "--platform-xml", help="custom cppcheck platform XML (overrides --platform)"
    )
    p.add_argument("--rule-texts", default="${MISRA_RULE_TEXTS}", help="headlines path")
    p.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="D",
        help="extra -D define for cppcheck (repeatable)",
    )
    p.set_defaults(func=_cmd_init)


class _AnalysisError(Exception):
    """Carries the exit code for a failed analysis pipeline."""

    def __init__(self, code: int):
        self.code = code


def _analyse(cfg, *, inline_suppr: bool = True) -> tuple[Report, dbmod.ScopeCoverage]:
    """Shared pipeline: db -> scope-check -> engine -> in-scope Report.

    Raises _AnalysisError(code) on any gate so callers just propagate the code.
    With inline_suppr=False, cppcheck-suppress comments are ignored (used by the
    deviation staleness check).
    """
    if cfg.rule_texts is None:
        _err.print(
            "[yellow]note:[/] no MISRA rule-texts found (set $MISRA_RULE_TEXTS "
            "or \\[rules].texts). Findings will be tagged category: unknown. "
            "See https://github.com/aajll/misch/blob/master/docs/rule-texts.md."
        )
    rules = load_headlines(cfg.rule_texts) if cfg.rule_texts else {}

    try:
        db_path = dbmod.resolve_compile_db(cfg)
    except dbmod.DbError as exc:
        _err.print(f"[red]compile-db error:[/] {exc}")
        raise _AnalysisError(2) from None

    coverage = dbmod.classify_files(cfg, db_path)
    if not coverage.ok():
        _err.print(
            f"[red]scope error:[/] {len(coverage.unattributed)} file(s) match "
            "neither \\[project].scope nor \\[project].exclude. Classify them "
            "explicitly (audit boundary must be enumerated):"
        )
        for f in coverage.unattributed:
            _err.print(f"  [red]?[/] {f}")
        raise _AnalysisError(2)

    try:
        findings = cppcheck.run(cfg, db_path, rules, inline_suppr=inline_suppr)
    except EngineError as exc:
        _err.print(f"[red]engine error:[/] {exc}")
        raise _AnalysisError(2) from None

    # Post-filter is authoritative: only report in-scope, non-excluded
    # locations. Findings at in-tree locations that match neither scope nor
    # exclude (headers never appear in the compile DB, so classify_files
    # cannot gate them) are a hard error, not a silent drop.
    findings, unattributed = dbmod.partition_findings(cfg, findings)
    if unattributed:
        by_file = Counter(f.file for f in unattributed)
        _err.print(
            f"[red]scope error:[/] {len(unattributed)} finding(s) at "
            "location(s) matching neither \\[project].scope nor "
            "\\[project].exclude. Classify them explicitly "
            "(audit boundary must be enumerated):"
        )
        for file, n in sorted(by_file.items()):
            _err.print(f"  [red]?[/] {file} ({n})")
        raise _AnalysisError(2)
    report = Report(
        findings=findings,
        analysed_files=coverage.analysed,
        excluded_files=coverage.excluded,
    )
    return report, coverage


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config, platform_name=args.platform)
    except ConfigError as exc:
        _err.print(f"[red]config error:[/] {exc}")
        return 2

    try:
        with _err.status("Running analysis..."):
            report, coverage = _analyse(cfg)
    except _AnalysisError as exc:
        return exc.code

    _emit(cfg, report, coverage, args)

    if not args.baseline:
        return 1 if report.misra() else 0

    # Ratchet mode: report everything but fail only on findings not in baseline.
    baseline = load_baseline(cfg.baseline_path)
    if not baseline:
        _err.print(
            f"[yellow]note:[/] --baseline set but {cfg.baseline_path.name} is "
            "empty/absent. Run [bold]misch baseline[/] to create it."
        )
    d = bl_diff(report.misra(), baseline)
    render_baseline_summary(d, cfg.baseline_path)
    return 1 if d.new else 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config, platform_name=args.platform)
    except ConfigError as exc:
        _err.print(f"[red]config error:[/] {exc}")
        return 2
    try:
        with _err.status("Running analysis..."):
            report, _ = _analyse(cfg)
    except _AnalysisError as exc:
        return exc.code

    path = args.baseline_file or cfg.baseline_path
    n = write_baseline(path, report.misra())
    _err.print(
        f"[green]wrote baseline[/] {path}: {n} finding(s) accepted. "
        "Future [bold]misch run --baseline[/] fails only on new ones."
    )
    return 0


def _cmd_deviations(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config, platform_name=args.platform)
    except ConfigError as exc:
        _err.print(f"[red]config error:[/] {exc}")
        return 2

    known = set(load_headlines(cfg.rule_texts)) if cfg.rule_texts else set()

    record = DeviationRecord()
    record.deviations.extend(
        harvest_inline(discover_sources(cfg), cfg.project_root, known)
    )
    if cfg.suppressions_path and cfg.suppressions_path.is_file():
        record.deviations.extend(
            parse_suppressions_file(cfg.suppressions_path, cfg.project_root, known)
        )

    if args.check_stale:
        try:
            # An unsuppressed run reveals what each suppression actually hides.
            with _err.status("Running analysis..."):
                report, _ = _analyse(cfg, inline_suppr=False)
            record.stale = find_stale(record.deviations, report.findings)
        except _AnalysisError:
            _err.print("[yellow]note:[/] staleness check skipped (analysis failed).")

    render_dev_terminal(record)
    for out in [{"format": f} for f in args.format]:
        if out["format"] == "md":
            path = args.output or (cfg.build_dir / "deviations.md")
            render_dev_markdown(record, path)
            _err.print(f"[dim]wrote deviation record to {path}[/]")
        else:
            fmt = out["format"]
            _err.print(f"[yellow]note:[/] deviations format {fmt!r} unsupported")

    return 0 if record.ok() else 1


def _emit(cfg, report: Report, coverage, args: argparse.Namespace) -> None:
    outputs = [{"format": f} for f in args.format] if args.format else list(cfg.outputs)
    for out in outputs:
        fmt = out["format"]
        if fmt == "terminal":
            render_terminal(report, coverage, verbose=getattr(args, "verbose", False))
        elif fmt == "json":
            path = args.output or Path(out.get("path", cfg.build_dir / "misra.json"))
            render_json(report, coverage, path)
            _err.print(f"[dim]wrote JSON findings to {path}[/]")
        elif fmt == "sarif":
            path = args.output or Path(out.get("path", cfg.build_dir / "misra.sarif"))
            render_sarif(report, path)
            _err.print(f"[dim]wrote SARIF to {path}[/]")
        else:
            _err.print(
                f"[yellow]note:[/] output format {fmt!r} unsupported "
                "(terminal/json/sarif)"
            )


def _cmd_init(args: argparse.Namespace) -> int:
    out: Path = args.output
    params = ScaffoldParams(
        scope=args.scope or ["src/"],
        exclude=args.exclude or ["tests/", "subprojects/"],
        db_source=args.db,
        db_path=args.db_path,
        platform_preset=args.platform,
        platform_xml=args.platform_xml,
        rule_texts=args.rule_texts,
        defines=args.define,
        scaffolded=args.scaffold,
    )
    try:
        written = write_project_files(out, params, force=args.force)
    except ScaffoldConflict as exc:
        _err.print("[red]refusing to overwrite existing init target(s):[/]")
        for path in exc.paths:
            _err.print(f"  [red]-[/] {path}")
        _err.print("Pass [bold]--force[/] to replace every listed target.")
        return 2
    except ScaffoldPathError as exc:
        _err.print("[red]cannot create regular init file(s) at these paths:[/]")
        for path in exc.paths:
            _err.print(f"  [red]-[/] {path}")
        return 2

    if args.scaffold:
        _err.print(f"[green]scaffolded[/] {out.parent / 'analysis'}")
    _err.print(
        f"[green]wrote {len(written)} file(s)[/] including {out}. "
        "Review scope/exclude and the compile-DB source, then run "
        "[bold]misch run[/]."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
