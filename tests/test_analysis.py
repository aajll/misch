"""Tests for analysis.analyse() using in-memory fake adapters.

Exercises every gate path without cppcheck on PATH or real compilation databases.
"""

from __future__ import annotations

from pathlib import Path

from misch.analysis import (
    AnalysisSuccess,
    EngineFailure,
    ScopeFailure,
)
from misch.config import Config
from misch.db import DbError, ScopeCoverage
from misch.engine.cppcheck import EngineError
from misch.report.model import Finding

# ---------------------------------------------------------------------------
# Fake adapters — configurable in-memory implementations of the protocols
# ---------------------------------------------------------------------------


class FakeDbAdapter:
    """Configurable implementation of DbAdapter for tests."""

    def __init__(
        self,
        resolve_result: Path | None = None,
        resolve_error: str | None = None,
        classify_coverage: ScopeCoverage | None = None,
        partition_in_scope: list[Finding] | None = None,
        partition_unattributed: list[Finding] | None = None,
    ):
        self._resolve_result = resolve_result or Path("/fake/compile_commands.json")
        self._resolve_error = resolve_error
        self._classify_coverage = classify_coverage or ScopeCoverage(
            analysed=["src/a.c"], excluded=[], unattributed=[]
        )
        self._partition_in_scope = partition_in_scope or []
        self._partition_unattributed = partition_unattributed or []

    def resolve(self, cfg: Config) -> Path:
        if self._resolve_error is not None:
            raise DbError(self._resolve_error)
        return self._resolve_result

    def classify_files(self, cfg: Config, db_path: Path) -> ScopeCoverage:
        return self._classify_coverage

    def partition_findings(
        self, cfg: Config, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]:
        if self._partition_in_scope is not None:
            return (self._partition_in_scope, self._partition_unattributed)
        return (findings, [])


class FakeEngineAdapter:
    """Configurable implementation of EngineAdapter for tests."""

    def __init__(
        self,
        run_result: list[Finding] | None = None,
        run_error: str | None = None,
    ):
        self.run_result = run_result or []
        self.run_error = run_error
        # Track last call arguments for verification
        self.last_inline_suppr: bool | None = None

    def run(
        self,
        cfg: Config,
        db_path: Path,
        rules: dict,
        *,
        inline_suppr: bool = True,
    ) -> list[Finding]:
        self.last_inline_suppr = inline_suppr
        if self.run_error is not None:
            raise EngineError(self.run_error)
        return self.run_result


# ---------------------------------------------------------------------------
# Helpers — factory functions for test data
# ---------------------------------------------------------------------------


def _finding(**kw) -> Finding:
    base = dict(rule_id="misra-c2012-11.4", message="msg", file="src/a.c", line=10)
    base.update(kw)
    return Finding(**base)


def _cfg(tmp_path: Path, scope=None, exclude=None, rule_texts=None) -> Config:
    cfgfile = tmp_path / "misra.toml"
    cfgfile.write_text("[project]\n")
    return Config(
        project_root=tmp_path,
        scope=scope or ["src/"],
        exclude=exclude or [],
        db_source="existing",
        db_path=None,
        platform="unix64",
        defines=[],
        rule_texts=rule_texts,
        outputs=[{"format": "terminal"}],
        baseline_path=tmp_path / "misra-baseline.json",
        suppressions_path=None,
    )


# ---------------------------------------------------------------------------
# Tests — one per gate path
# ---------------------------------------------------------------------------


def test_db_resolution_failure_returns_db_failure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = FakeDbAdapter(resolve_error="meson not found on PATH")
    engine = FakeEngineAdapter()

    result = analyse(cfg, db, engine)

    from misch.analysis import DbFailure

    assert isinstance(result, DbFailure)
    assert result.message == "meson not found on PATH"


def test_scope_classify_unattributed_returns_scope_failure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    coverage = ScopeCoverage(
        analysed=["src/a.c"], excluded=[], unattributed=["src/unknown.c"]
    )
    db = FakeDbAdapter(classify_coverage=coverage)
    engine = FakeEngineAdapter()

    result = analyse(cfg, db, engine)

    assert isinstance(result, ScopeFailure)
    assert "src/unknown.c" in result.unattributed
    assert "1 file(s)" in result.message


def test_engine_failure_returns_engine_failure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = FakeDbAdapter()
    engine = FakeEngineAdapter(run_error="cppcheck exited with code 1")

    result = analyse(cfg, db, engine)

    assert isinstance(result, EngineFailure)
    assert result.message == "cppcheck exited with code 1"


def test_successful_analysis_returns_report_and_coverage(tmp_path: Path) -> None:
    findings = [_finding()]
    coverage = ScopeCoverage(
        analysed=["src/a.c"], excluded=["test/b.c"], unattributed=[]
    )
    db = FakeDbAdapter(
        classify_coverage=coverage,
        partition_in_scope=findings,
        partition_unattributed=[],
    )
    engine = FakeEngineAdapter(run_result=findings)
    cfg = _cfg(tmp_path)

    result = analyse(cfg, db, engine)

    assert isinstance(result, AnalysisSuccess)
    assert result.report.findings == findings
    assert result.coverage.analysed == ["src/a.c"]
    assert result.coverage.excluded == ["test/b.c"]


def test_absent_rule_texts_produces_notice_not_printed(tmp_path: Path) -> None:
    coverage = ScopeCoverage(analysed=["src/a.c"], excluded=[], unattributed=[])
    db = FakeDbAdapter(classify_coverage=coverage)
    engine = FakeEngineAdapter(run_result=[])
    cfg = _cfg(tmp_path, rule_texts=None)

    result = analyse(cfg, db, engine)

    assert isinstance(result, AnalysisSuccess)
    assert len(result.notices) == 1
    assert result.notices[0].key == "rule_texts_absent"


def test_finding_partition_unattributed_returns_scope_failure(tmp_path: Path) -> None:
    findings = [_finding(file="src/a.c"), _finding(file="header/h.h")]
    coverage = ScopeCoverage(analysed=["src/a.c"], excluded=[], unattributed=[])
    db = FakeDbAdapter(
        classify_coverage=coverage,
        partition_in_scope=[_finding(file="src/a.c")],
        partition_unattributed=[_finding(file="header/h.h")],
    )
    engine = FakeEngineAdapter(run_result=findings)
    cfg = _cfg(tmp_path)

    result = analyse(cfg, db, engine)

    assert isinstance(result, ScopeFailure)
    assert "header/h.h" in result.unattributed
    assert "1 finding(s)" in result.message


def test_inline_suppr_false_flows_to_engine_adapter(tmp_path: Path) -> None:
    coverage = ScopeCoverage(analysed=["src/a.c"], excluded=[], unattributed=[])
    db = FakeDbAdapter(classify_coverage=coverage)
    engine = FakeEngineAdapter(run_result=[])
    cfg = _cfg(tmp_path)

    analyse(cfg, db, engine, inline_suppr=False)

    assert engine.last_inline_suppr is False


def test_inline_suppr_true_is_default(tmp_path: Path) -> None:
    coverage = ScopeCoverage(analysed=["src/a.c"], excluded=[], unattributed=[])
    db = FakeDbAdapter(classify_coverage=coverage)
    engine = FakeEngineAdapter(run_result=[])
    cfg = _cfg(tmp_path)

    analyse(cfg, db, engine)

    assert engine.last_inline_suppr is True


# Import the function under test at module level for all tests
from misch.analysis import analyse  # noqa: E402
