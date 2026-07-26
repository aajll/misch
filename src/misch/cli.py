"""`misch` command-line entry point."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from rich.console import Console
from rich_argparse import RichHelpFormatter

from . import __version__, scaffold
from .config_schema import INIT_DEFAULTS
from .execution import run

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

    p = add_parser("run", help="analyse a project")
    _add_config_arg(p)
    p.add_argument("--format", action="append", default=[], help="override outputs")
    p.add_argument("--output", type=Path, help="path for a file format")
    p.add_argument("--baseline", action="store_true", help="fail only on new findings")
    p.add_argument("-v", "--verbose", action="store_true", help="print diagnostics")

    _add_init_parser(add_parser)

    p = add_parser("baseline", help="snapshot current findings as the baseline")
    _add_config_arg(p)
    p.add_argument("--baseline-file", type=Path, help="override the baseline path")

    p = add_parser("deviations", help="harvest + validate MISRA deviations")
    _add_config_arg(p)
    p.add_argument("--check-stale", action="store_true", help="flag stale suppressions")
    p.add_argument("--format", action="append", default=[], help="e.g. --format md")
    p.add_argument("--output", type=Path, help="path for the Markdown record")

    args = parser.parse_args(argv)
    return _cmd_init(args) if args.cmd == "init" else run(args)


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default="misra.toml", type=Path)
    parser.add_argument("--profile", help="profile name to apply (e.g. arm, aarch64)")


def _add_init_parser(add_parser) -> None:
    p = add_parser("init", help="generate a misra.toml template")
    p.add_argument("-o", "--output", type=Path, default=Path("misra.toml"))
    p.add_argument("--force", action="store_true", help="overwrite generated files")
    p.add_argument("--scaffold", action="store_true", help="create analysis/ assets")
    p.add_argument("--db", choices=["meson", "cmake", "existing"])
    p.add_argument("--db-path", help="compile_commands.json path")
    p.add_argument("--scope", action="append", default=[], metavar="GLOB")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p.add_argument("--platform", default=INIT_DEFAULTS.platform)
    p.add_argument("--platform-xml", help="custom cppcheck platform XML")
    p.add_argument("--rule-texts", default=INIT_DEFAULTS.rule_texts)
    p.add_argument("--define", action="append", default=[], metavar="D")


def _cmd_init(args: argparse.Namespace) -> int:
    out: Path = args.output
    try:
        db_source, db_path = (
            (args.db, args.db_path)
            if args.db
            else ("existing", args.db_path)
            if args.db_path
            else scaffold.detect_db_source(out.parent)
        )
    except scaffold.ScaffoldDbDetectionError as exc:
        _err.print(
            f"[red]init error:[/] {exc}; pass --db meson, --db cmake, or --db existing."
        )
        return 2

    params = scaffold.ScaffoldParams(
        scope=args.scope or list(INIT_DEFAULTS.scope),
        exclude=args.exclude or list(INIT_DEFAULTS.exclude),
        db_source=db_source,
        db_path=db_path,
        platform_preset=args.platform,
        platform_xml=args.platform_xml,
        rule_texts=args.rule_texts,
        defines=args.define,
        scaffolded=args.scaffold,
    )
    try:
        written = scaffold.write_project_files(out, params, force=args.force)
    except scaffold.ScaffoldConflict as exc:
        _err.print("[red]refusing to overwrite existing init target(s):[/]")
        for path in exc.paths:
            _err.print(f"  [red]-[/] {path}")
        _err.print("Pass [bold]--force[/] to replace every listed target.")
        return 2
    except scaffold.ScaffoldPathError as exc:
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
