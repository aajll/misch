"""Unit tests for the execution orchestration seam."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import misch.execution as execution
from misch.config import Config, ConfigError
from misch.db import ScopeCoverage
from misch.report.deviations import DeviationRecord
from misch.report.model import Finding


class FakeDbAdapter:
    """In-memory database adapter following the analysis test pattern."""

    def resolve(self, cfg: Config) -> Path:
        return Path("/fake/compile_commands.json")

    def classify_files(self, cfg: Config, db_path: Path) -> ScopeCoverage:
        return ScopeCoverage(analysed=["src/a.c"], excluded=[], unattributed=[])

    def partition_findings(
        self, cfg: Config, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]:
        return findings, []


class FakeEngineAdapter:
    """In-memory engine adapter that records suppression mode."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        self.inline_suppr: bool | None = None

    def run(
        self, cfg: Config, db_path: Path, rules: dict, *, inline_suppr: bool
    ) -> list[Finding]:
        self.inline_suppr = inline_suppr
        return self.findings


def _cfg(tmp_path: Path) -> Config:
    return Config(
        project_root=tmp_path,
        scope=["src/"],
        exclude=[],
        db_source="existing",
        db_path=None,
        platform="unix64",
        defines=[],
        rule_texts=None,
        outputs=[{"format": "terminal"}],
        baseline_path=tmp_path / "misra-baseline.json",
        suppressions_path=None,
    )


def _finding() -> Finding:
    return Finding("misra-c2012-11.4", "msg", "src/a.c", 10)


def _args(cmd: str, **kwargs) -> argparse.Namespace:
    values = {
        "cmd": cmd,
        "config": Path("misra.toml"),
        "profile": None,
        "format": [],
        "output": None,
        "baseline": False,
        "verbose": False,
        "baseline_file": None,
        "check_stale": False,
    }
    values.update(kwargs)
    return argparse.Namespace(**values)


def _install_fakes(
    monkeypatch, tmp_path: Path, findings: list[Finding]
) -> FakeEngineAdapter:
    engine = FakeEngineAdapter(findings)
    monkeypatch.setattr(execution, "load", lambda *_args, **_kwargs: _cfg(tmp_path))
    monkeypatch.setattr(execution, "DbAdapterImpl", FakeDbAdapter)
    monkeypatch.setattr(execution, "EngineAdapterImpl", lambda: engine)
    monkeypatch.setattr(execution, "_err", MagicMock())
    return engine


def test_run_uses_fake_adapters_and_returns_findings_exit_code(
    monkeypatch, tmp_path: Path
):
    engine = _install_fakes(monkeypatch, tmp_path, [_finding()])
    rendered = MagicMock()
    monkeypatch.setattr(execution, "render", rendered)

    assert execution.run(_args("run")) == 1
    assert engine.inline_suppr is True
    rendered.assert_called_once()


def test_baseline_uses_fake_adapters_and_writes_snapshot(monkeypatch, tmp_path: Path):
    _install_fakes(monkeypatch, tmp_path, [_finding()])
    wrote = MagicMock(return_value=1)
    monkeypatch.setattr(execution, "write_baseline", wrote)

    assert execution.run(_args("baseline")) == 0
    wrote.assert_called_once_with(tmp_path / "misra-baseline.json", [_finding()])


def test_deviations_stale_check_uses_unsuppressed_fake_analysis(
    monkeypatch, tmp_path: Path
):
    finding = _finding()
    engine = _install_fakes(monkeypatch, tmp_path, [finding])
    record = MagicMock(spec=DeviationRecord)
    record.ok.return_value = True
    harvested = MagicMock(return_value=record)
    monkeypatch.setattr(execution, "harvest", harvested)
    monkeypatch.setattr(execution, "render_dev_terminal", MagicMock())

    assert execution.run(_args("deviations", check_stale=True)) == 0
    assert engine.inline_suppr is False
    assert harvested.call_args.kwargs["findings"] == [finding]


def test_config_failure_returns_error_exit_code_without_stream_capture(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ConfigError("bad configuration")

    monkeypatch.setattr(execution, "load", fail)
    monkeypatch.setattr(execution, "_err", MagicMock())

    assert execution.run(_args("run")) == 2
