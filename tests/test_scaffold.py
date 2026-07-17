"""Behavior tests for minimal and scaffolded project initialization."""

from __future__ import annotations

import json
from importlib.resources import files as resource_files
from pathlib import Path

from misch.cli import main
from misch.config import load
from misch.report.baseline import SCHEMA, write_baseline
from misch.scaffold import ScaffoldParams, build_config


def test_build_config_standard_keeps_optional_deviations():
    text = build_config(ScaffoldParams())
    lines = [line.strip() for line in text.splitlines()]

    assert 'texts = "${MISRA_RULE_TEXTS}"' in lines
    assert '# suppressions = "misra-deviations.txt"' in lines
    assert "[baseline]" not in lines


def test_scaffold_cli_creates_documented_loadable_layout(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MISRA_RULE_TEXTS", raising=False)
    out = tmp_path / "misra.toml"

    assert main(["init", "--scaffold", "-o", str(out)]) == 0

    expected = {
        out,
        tmp_path / "analysis/README.md",
        tmp_path / "analysis/rules/README.md",
        tmp_path / "analysis/deviations/misra-deviations.txt",
        tmp_path / "analysis/baseline/README.md",
    }
    assert all(path.is_file() for path in expected)
    assert not (tmp_path / "analysis/rules/misra-rules.md").exists()
    assert not (tmp_path / "analysis/baseline/misra-baseline.json").exists()

    templates = resource_files("misch").joinpath("templates", "analysis")
    for generated in expected - {out}:
        relative = generated.relative_to(tmp_path / "analysis")
        packaged = templates.joinpath(*relative.parts)
        assert packaged.is_file()
        assert generated.read_text() == packaged.read_text(encoding="utf-8")

    cfg = load(out)
    assert cfg.rule_texts is None
    assert cfg.suppressions_path == (
        tmp_path / "analysis/deviations/misra-deviations.txt"
    )
    assert cfg.baseline_path == (tmp_path / "analysis/baseline/misra-baseline.json")

    analysis_help = (tmp_path / "analysis/README.md").read_text()
    rules_help = (tmp_path / "analysis/rules/README.md").read_text()
    baseline_help = (tmp_path / "analysis/baseline/README.md").read_text()
    deviations = (tmp_path / "analysis/deviations/misra-deviations.txt").read_text()
    assert "misch baseline" in analysis_help
    assert "licensed" in rules_help.lower()
    assert "Appendix A Summary of guidelines" in rules_help
    assert "explicitly accepts" in baseline_help
    assert "misch deviations --check-stale" in deviations


def test_scaffold_preserves_custom_rule_texts_setting(tmp_path: Path):
    out = tmp_path / "misra.toml"

    assert (
        main(
            [
                "init",
                "--scaffold",
                "--rule-texts",
                "private/headlines.txt",
                "-o",
                str(out),
            ]
        )
        == 0
    )

    assert 'texts = "private/headlines.txt"' in out.read_text()


def test_scaffold_collision_refuses_all_writes(tmp_path: Path, capsys):
    out = tmp_path / "misra.toml"
    existing = tmp_path / "analysis/deviations/misra-deviations.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("KEEP ME\n")

    assert main(["init", "--scaffold", "-o", str(out)]) == 2

    assert existing.read_text() == "KEEP ME\n"
    assert not out.exists()
    assert not (tmp_path / "analysis/README.md").exists()
    assert "refusing to overwrite" in capsys.readouterr().err


def test_scaffold_force_replaces_all_generated_targets(tmp_path: Path):
    out = tmp_path / "misra.toml"
    args = ["init", "--scaffold", "-o", str(out)]
    assert main(args) == 0

    deviations = tmp_path / "analysis/deviations/misra-deviations.txt"
    out.write_text("old config\n")
    deviations.write_text("old deviations\n")

    assert main([*args, "--force"]) == 0
    assert "[project]" in out.read_text()
    assert "old deviations" not in deviations.read_text()


def test_scaffold_reports_non_directory_parent_without_partial_writes(
    tmp_path: Path, capsys
):
    out = tmp_path / "misra.toml"
    blocker = tmp_path / "analysis"
    blocker.write_text("not a directory\n")

    assert main(["init", "--scaffold", "-o", str(out)]) == 2
    assert not out.exists()
    assert blocker.read_text() == "not a directory\n"
    assert "cannot create regular init" in capsys.readouterr().err


def test_scaffold_force_does_not_replace_directory_with_file(tmp_path: Path, capsys):
    out = tmp_path / "misra.toml"
    out.mkdir()

    assert main(["init", "--scaffold", "--force", "-o", str(out)]) == 2
    assert out.is_dir()
    assert not (tmp_path / "analysis").exists()
    assert "cannot create regular init" in capsys.readouterr().err


def test_scaffold_baseline_path_accepts_canonical_snapshot(tmp_path: Path):
    out = tmp_path / "misra.toml"
    assert main(["init", "--scaffold", "-o", str(out)]) == 0
    cfg = load(out)

    assert write_baseline(cfg.baseline_path, []) == 0
    document = json.loads(cfg.baseline_path.read_text())
    assert document == {"schema": SCHEMA, "fingerprints": {}}
