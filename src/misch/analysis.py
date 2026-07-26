"""Analysis transaction types, failure modes, and adapter protocols.

Declares the public interface for running a full MISRA analysis pass. The CLI
commands will call this interface instead of owning the pipeline themselves.

This module is a pure definition layer — no existing code is modified and nothing
imports it yet.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import Config
from .db import DbError, ScopeCoverage
from .engine.cppcheck import EngineError
from .report.headlines import load_headlines
from .report.model import Finding, Report

# ---------------------------------------------------------------------------
# Notices — non-fatal observations attached to success results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Notice:
    """A non-fatal observation emitted during analysis.

    Notices are *not* printed by the transaction itself; they are carried on the
    success result so downstream consumers (renderers, CI reporters) can decide
    how to surface them.
    """

    key: str  # stable machine-readable identifier, e.g. "rule_texts_absent"
    message: str  # human-readable description


# ---------------------------------------------------------------------------
# Failure types — structured error data, not exit codes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnalysisFailure:
    """Base for all analysis failure variants."""

    kind: str


@dataclass(frozen=True, slots=True)
class DbFailure(AnalysisFailure):
    """The compile database could not be resolved or produced.

    Raised when the configured build tool is missing, fails to configure, or
    does not emit a valid ``compile_commands.json``.
    """

    kind: str = field(default="db", init=False)
    message: str


@dataclass(frozen=True, slots=True)
class ScopeFailure(AnalysisFailure):
    """One or more files could not be classified into scope / exclude.

    Carries the unattributed paths so a caller can report them precisely rather
    than returning an opaque exit code.
    """

    kind: str = field(default="scope", init=False)
    unattributed: list[str]  # project-relative POSIX paths
    message: str


@dataclass(frozen=True, slots=True)
class EngineFailure(AnalysisFailure):
    """The static-analysis engine failed to run or produce valid output.

    Covers cppcheck not found on PATH, non-zero exit with no XML, and XML parse
    errors.
    """

    kind: str = field(default="engine", init=False)
    message: str


# ---------------------------------------------------------------------------
# Success result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnalysisSuccess:
    """A completed analysis pass with findings and scope coverage."""

    report: Report
    coverage: ScopeCoverage  # re-exported below for convenience
    notices: list[Notice] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------


AnalysisResult = AnalysisSuccess | AnalysisFailure


# ---------------------------------------------------------------------------
# Adapter protocols — injectable seams for the analysis pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleInfo:
    """Thin handle to a headline entry; mirrors ``report.headlines.RuleInfo``."""

    category: str
    headline: str


class DbAdapter(Protocol):
    """Compile-database resolution and scope classification.

    Implemented by the ``db`` module in production; replaceable with a mock or
    alternative backend in tests.
    """

    @abstractmethod
    def resolve(self, cfg: Config) -> Path: ...

    @abstractmethod
    def classify_files(self, cfg: Config, db_path: Path) -> ScopeCoverage: ...

    @abstractmethod
    def partition_findings(
        self, cfg: Config, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]: ...


class EngineAdapter(Protocol):
    """Static-analysis engine execution.

    Implemented by ``engine.cppcheck`` in production; replaceable with a mock or
    alternative engine backend.
    """

    @abstractmethod
    def run(
        self,
        cfg: Config,
        db_path: Path,
        rules: dict[str, RuleInfo],
        *,
        inline_suppr: bool = True,
    ) -> list[Finding]: ...


# ---------------------------------------------------------------------------
# Transaction entry point — signature only (stub body)
# ---------------------------------------------------------------------------


def analyse(
    cfg: Config,
    db: DbAdapter,
    engine: EngineAdapter,
    *,
    inline_suppr: bool = True,
) -> AnalysisResult:
    """Run the full analysis pipeline and return structured results.

    Delegates to injected adapters for database resolution, scope classification,
    and engine execution. Returns an ``AnalysisSuccess`` with findings, coverage,
    and optional notices on a clean run, or one of the ``AnalysisFailure``
    variants when a gate fails.

    Args:
        cfg: Resolved project configuration.
        db: Adapter for compile-database operations.
        engine: Adapter for running the static-analysis backend.
        inline_suppr: When ``False``, inline ``cppcheck-suppress`` comments are
            ignored (used by the deviation staleness check).

    Returns:
        An ``AnalysisSuccess`` or an ``AnalysisFailure`` variant.
    """
    notices: list[Notice] = []

    # Load rule headlines; emit notice if absent rather than printing to stderr.
    rules: dict[str, RuleInfo] = {}
    if cfg.rule_texts is None:
        notices.append(
            Notice(
                key="rule_texts_absent",
                message=(
                    "no MISRA rule-texts found (set $MISRA_RULE_TEXTS or "
                    "[rules].texts). Findings will be tagged category: unknown."
                ),
            )
        )
    else:
        loaded = load_headlines(cfg.rule_texts)
        # Map headline RuleInfo to our local shape (category is StrEnum -> str).
        rules = {
            k: RuleInfo(category=str(v.category), headline=v.headline)
            for k, v in loaded.items()
        }

    # Resolve the compile database.
    try:
        db_path = db.resolve(cfg)
    except DbError as exc:
        return DbFailure(message=str(exc))

    # Classify scope: every DB file must be explicitly attributed.
    coverage = db.classify_files(cfg, db_path)
    if not coverage.ok():
        return ScopeFailure(
            unattributed=list(coverage.unattributed),
            message=(
                f"{len(coverage.unattributed)} file(s) match neither "
                "[project].scope nor [project].exclude."
            ),
        )

    # Run the static-analysis engine.
    try:
        findings = engine.run(cfg, db_path, rules, inline_suppr=inline_suppr)
    except EngineError as exc:
        return EngineFailure(message=str(exc))

    # Post-filter: only report in-scope locations; unattributed is hard error.
    findings, unattributed = db.partition_findings(cfg, findings)
    if unattributed:
        unattributed_files = [f.file for f in unattributed]
        return ScopeFailure(
            unattributed=unattributed_files,
            message=(
                f"{len(unattributed)} finding(s) at location(s) matching neither "
                "[project].scope nor [project].exclude."
            ),
        )

    report = Report(
        findings=findings,
        analysed_files=coverage.analysed,
        excluded_files=coverage.excluded,
    )
    return AnalysisSuccess(report=report, coverage=coverage, notices=notices)


# ---------------------------------------------------------------------------
# Concrete adapter implementations — wire real modules for production use
# ---------------------------------------------------------------------------


class DbAdapterImpl:
    """Delegates to the ``db`` module functions."""

    def resolve(self, cfg: Config) -> Path:
        from . import db as _dbmod  # noqa: F811

        return _dbmod.resolve_compile_db(cfg)

    def classify_files(self, cfg: Config, db_path: Path) -> ScopeCoverage:
        from . import db as _dbmod

        return _dbmod.classify_files(cfg, db_path)

    def partition_findings(
        self, cfg: Config, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]:
        from . import db as _dbmod

        return _dbmod.partition_findings(cfg, findings)


class EngineAdapterImpl:
    """Delegates to the ``engine.cppcheck`` module."""

    def run(
        self,
        cfg: Config,
        db_path: Path,
        rules: dict[str, RuleInfo],
        *,
        inline_suppr: bool = True,
    ) -> list[Finding]:
        from .engine import cppcheck as _cpp

        # Map local RuleInfo back to headline RuleInfo for the engine.
        from .report.headlines import RuleInfo as HeadlineRuleInfo
        from .report.model import Category

        hrules = {
            k: HeadlineRuleInfo(category=Category(v.category), headline=v.headline)
            for k, v in rules.items()
        }
        return _cpp.run(cfg, db_path, hrules, inline_suppr=inline_suppr)
