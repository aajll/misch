"""Behavior tests for minimal and scaffolded project initialization."""

from __future__ import annotations

import json
from importlib.resources import files as resource_files
from pathlib import Path

import pytest

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

    assert main(["init", "--db", "meson", "--scaffold", "-o", str(out)]) == 0

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
                "--db",
                "meson",
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

    assert main(["init", "--db", "meson", "--scaffold", "-o", str(out)]) == 2

    assert existing.read_text() == "KEEP ME\n"
    assert not out.exists()
    assert not (tmp_path / "analysis/README.md").exists()
    assert "refusing to overwrite" in capsys.readouterr().err


def test_scaffold_force_replaces_all_generated_targets(tmp_path: Path):
    out = tmp_path / "misra.toml"
    args = ["init", "--db", "meson", "--scaffold", "-o", str(out)]
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

    assert main(["init", "--db", "meson", "--scaffold", "-o", str(out)]) == 2
    assert not out.exists()
    assert blocker.read_text() == "not a directory\n"
    assert "cannot create regular init" in capsys.readouterr().err


def test_scaffold_force_does_not_replace_directory_with_file(tmp_path: Path, capsys):
    out = tmp_path / "misra.toml"
    out.mkdir()

    assert main(["init", "--db", "meson", "--scaffold", "--force", "-o", str(out)]) == 2
    assert out.is_dir()
    assert not (tmp_path / "analysis").exists()
    assert "cannot create regular init" in capsys.readouterr().err


def test_scaffold_reports_blocker_above_missing_output_directory(
    tmp_path: Path, capsys
):
    blocker = tmp_path / "project"
    blocker.write_text("not a directory\n")
    out = blocker / "config/misra.toml"

    assert main(["init", "--db", "meson", "--scaffold", "-o", str(out)]) == 2

    assert blocker.read_text() == "not a directory\n"
    assert "cannot create regular init" in capsys.readouterr().err


@pytest.mark.parametrize("force", [False, True])
def test_scaffold_rejects_dangling_config_symlink_without_writes(
    tmp_path: Path, capsys, force: bool
):
    project = tmp_path / "project"
    project.mkdir()
    out = project / "misra.toml"
    external = tmp_path / "outside.toml"
    out.symlink_to(external)
    args = ["init", "--db", "meson", "--scaffold", "-o", str(out)]
    if force:
        args.append("--force")

    assert main(args) == 2

    assert out.is_symlink()
    assert not external.exists()
    assert not (project / "analysis").exists()
    assert "cannot create regular init" in capsys.readouterr().err


@pytest.mark.parametrize("force", [False, True])
def test_scaffold_rejects_symlinked_analysis_directory_without_writes(
    tmp_path: Path, capsys, force: bool
):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    analysis = project / "analysis"
    analysis.symlink_to(external, target_is_directory=True)
    out = project / "misra.toml"
    args = ["init", "--db", "meson", "--scaffold", "-o", str(out)]
    if force:
        args.append("--force")

    assert main(args) == 2

    assert analysis.is_symlink()
    assert list(external.iterdir()) == []
    assert not out.exists()
    assert "cannot create regular init" in capsys.readouterr().err


@pytest.mark.parametrize("force", [False, True])
def test_scaffold_rejects_generated_file_symlink_without_writes(
    tmp_path: Path, capsys, force: bool
):
    project = tmp_path / "project"
    rules = project / "analysis/rules"
    rules.mkdir(parents=True)
    external = tmp_path / "outside.md"
    linked_target = rules / "README.md"
    linked_target.symlink_to(external)
    out = project / "misra.toml"
    args = ["init", "--db", "meson", "--scaffold", "-o", str(out)]
    if force:
        args.append("--force")

    assert main(args) == 2

    assert linked_target.is_symlink()
    assert not external.exists()
    assert not out.exists()
    assert not (project / "analysis/README.md").exists()
    assert "cannot create regular init" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("marker", "source"),
    [("meson.build", "meson"), ("CMakeLists.txt", "cmake")],
)
def test_init_detects_a_single_build_system(tmp_path: Path, marker: str, source: str):
    (tmp_path / marker).write_text("# marker\n")
    out = tmp_path / "misra.toml"

    assert main(["init", "-o", str(out)]) == 0
    assert load(out).db_source == source


def test_init_detects_existing_database_and_writes_its_path(tmp_path: Path):
    db = tmp_path / "build/compile_commands.json"
    db.parent.mkdir()
    db.write_text("[]")
    out = tmp_path / "misra.toml"

    assert main(["init", "-o", str(out)]) == 0
    cfg = load(out)
    assert cfg.db_source == "existing"
    assert cfg.db_path == "build/compile_commands.json"


@pytest.mark.parametrize("force", [False, True])
def test_init_requires_explicit_db_when_detection_is_absent_or_ambiguous(
    tmp_path: Path, capsys, force: bool
):
    out = tmp_path / "misra.toml"
    (tmp_path / "meson.build").write_text("# marker\n")
    (tmp_path / "CMakeLists.txt").write_text("# marker\n")
    args = ["init", "-o", str(out)]
    if force:
        args.append("--force")

    assert main(args) == 2
    assert not out.exists()
    assert "--db meson" in capsys.readouterr().err


def test_init_requires_explicit_db_when_no_candidate_exists(tmp_path: Path, capsys):
    out = tmp_path / "misra.toml"

    assert main(["init", "-o", str(out)]) == 2
    assert not out.exists()
    assert "candidates: none" in capsys.readouterr().err


def test_init_explicit_db_overrides_detection(tmp_path: Path):
    (tmp_path / "meson.build").write_text("# marker\n")
    (tmp_path / "CMakeLists.txt").write_text("# marker\n")
    out = tmp_path / "misra.toml"

    assert main(["init", "--db", "cmake", "-o", str(out)]) == 0
    assert load(out).db_source == "cmake"


def test_init_db_path_selects_existing_without_detection(tmp_path: Path):
    out = tmp_path / "misra.toml"

    assert main(["init", "--db-path", "out/compile_commands.json", "-o", str(out)]) == 0
    cfg = load(out)
    assert cfg.db_source == "existing"
    assert cfg.db_path == "out/compile_commands.json"


def test_scaffold_baseline_path_accepts_canonical_snapshot(tmp_path: Path):
    out = tmp_path / "misra.toml"
    assert main(["init", "--db", "meson", "--scaffold", "-o", str(out)]) == 0
    cfg = load(out)

    assert write_baseline(cfg.baseline_path, []) == 0
    document = json.loads(cfg.baseline_path.read_text())
    assert document == {"schema": SCHEMA, "fingerprints": {}}
