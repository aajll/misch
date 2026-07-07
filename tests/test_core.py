"""Unit tests for the pieces that do not need cppcheck (model, scope, parser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from misch import __version__
from misch.cli import main
from misch.config import Config, ConfigError, load
from misch.db import DbError, _matches_any, classify_files, in_scope, resolve_compile_db
from misch.report.baseline import BaselineDiff
from misch.report.dev_render import render_markdown
from misch.report.deviations import Deviation, DeviationRecord
from misch.report.headlines import load_headlines
from misch.report.model import Category, Finding, is_misra_id
from misch.report.renderers import render_baseline_summary


def _finding(**kw) -> Finding:
    base = dict(rule_id="misra-c2012-11.4", message="msg", file="src/a.c", line=10)
    base.update(kw)
    return Finding(**base)


def test_fingerprint_is_line_independent():
    a = _finding(line=10)
    b = _finding(line=99)
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_ignores_volatile_message_tokens():
    a = _finding(message="value 12 in 'foo' is bad")
    b = _finding(message="value 87 in 'bar' is bad")
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_distinguishes_rule_and_file():
    assert _finding(rule_id="misra-c2012-8.7").fingerprint() != _finding().fingerprint()
    assert _finding(file="src/b.c").fingerprint() != _finding().fingerprint()


def test_fingerprint_symbol_disambiguates_same_rule_same_file():
    # Two 8.7 findings in one file share a generic message; the symbol keeps
    # them distinct so a second duplicate is not silently baselined.
    msg = "Functions and objects should not be defined with external linkage"
    a = _finding(rule_id="misra-c2012-8.7", message=msg, symbol="foo")
    b = _finding(rule_id="misra-c2012-8.7", message=msg, symbol="bar")
    assert a.fingerprint() != b.fingerprint()


def test_is_misra_id():
    assert is_misra_id("misra-c2012-11.4")
    assert is_misra_id("misra-c2012-dir-4.9")
    assert not is_misra_id("nullPointer")
    assert not is_misra_id("unusedFunction")


def test_scope_matching_directory_and_glob():
    assert _matches_any("src/a.c", ["src/"])
    assert _matches_any("src/sub/a.c", ["src/"])
    assert not _matches_any("srcextra/a.c", ["src/"])  # prefix must be a boundary
    assert _matches_any("x/gen.h", ["*.h"])
    assert not _matches_any("x/gen.c", ["*.h"])


def test_headlines_parser_tab_category_next_line_title(tmp_path: Path):
    f = tmp_path / "headlines.txt"
    f.write_text(
        "# comment\n"
        "Dir 4.9\t\tAdvisory\n"
        "A function should be used in preference to a function-like macro\n"
        "Rule 11.4 Advisory A conversion should not be performed\n"
    )
    rules = load_headlines(str(f))
    assert rules["misra-c2012-dir-4.9"].category is Category.ADVISORY
    assert "function-like macro" in rules["misra-c2012-dir-4.9"].headline
    assert rules["misra-c2012-11.4"].category is Category.ADVISORY
    assert rules["misra-c2012-11.4"].headline.startswith("A conversion")


def test_harvest_inline_forms_and_justification(tmp_path: Path):
    from misch.report.deviations import harvest_inline

    src = tmp_path / "a.c"
    src.write_text(
        "int a; /* cppcheck-suppress misra-c2012-8.7 ; @deviation library API */\n"
        "// cppcheck-suppress[misra-c2012-11.4, misra-c2012-11.6] ; reason: MMIO\n"
        "int c; // cppcheck-suppress misra-c2012-15.5\n"  # no justification
        "int d; // cppcheck-suppress misra-c2012-99.9 ; @deviation typo id\n"
    )
    known = {
        "misra-c2012-8.7",
        "misra-c2012-11.4",
        "misra-c2012-11.6",
        "misra-c2012-15.5",
    }
    devs = harvest_inline([src], tmp_path, known)

    assert len(devs) == 4
    assert devs[0].rule_ids == ["misra-c2012-8.7"] and devs[0].justified
    assert devs[1].rule_ids == ["misra-c2012-11.4", "misra-c2012-11.6"]
    assert devs[1].justification == "MMIO"
    assert not devs[2].justified  # 15.5 lacks a reason
    assert devs[3].unknown_ids == ["misra-c2012-99.9"]  # typo caught


def test_harvest_multiline_block_comment_justification_and_anchor(tmp_path: Path):
    """A cppcheck-suppress in a multi-line block comment: the justification
    spans continuation lines, and staleness must anchor where the comment
    closes (the code the suppression binds to), not at the token line."""
    from misch.report.deviations import find_stale, harvest_inline

    src = tmp_path / "a.c"
    src.write_text(
        "uint32_t\n"  # line 1: return type (K&R signature split)
        "/* cppcheck-suppress[misra-c2012-8.7] ; @deviation public API entry\n"  # 2
        " * point declared in a.h; referenced only by consumer TUs, so\n"  # 3
        " * cppcheck sees a single translation unit */\n"  # 4
        "compute(void)\n"  # 5: the finding lands here
        "{\n"
    )
    devs = harvest_inline([src], tmp_path, {"misra-c2012-8.7"})

    assert len(devs) == 1
    d = devs[0]
    assert d.rule_ids == ["misra-c2012-8.7"]
    # Full rationale reassembled across all three comment lines.
    assert "public API entry point declared in a.h" in d.justification
    assert "single translation unit" in d.justification
    # Anchored at the closing comment line (4), so the tolerance window reaches
    # the finding on the code line (5).
    assert d.line == 4

    finding = _finding(rule_id="misra-c2012-8.7", file="a.c", line=5)
    assert find_stale([d], [finding]) == []  # live, not falsely stale

    # And genuinely dead if nothing sits at the code line.
    elsewhere = _finding(rule_id="misra-c2012-8.7", file="a.c", line=99)
    assert find_stale([d], [elsewhere]) == ["a.c:4"]


def test_parse_suppressions_file_comment_is_justification(tmp_path: Path):
    from misch.report.deviations import parse_suppressions_file

    f = tmp_path / "suppressions.txt"
    f.write_text(
        "# vendor SDK is upstream-owned\n"
        "*:*/vendor/*\n"
        "\n"
        "misra-c2012-15.5\n"  # no preceding comment -> unjustified
    )
    devs = parse_suppressions_file(f, tmp_path, set())
    assert devs[0].rule_ids == ["*"] and "vendor" in devs[0].justification
    assert not devs[1].justified


def test_render_sarif_structure(tmp_path: Path):
    import json

    from misch.report.model import Report
    from misch.report.renderers import render_sarif

    report = Report(
        findings=[
            _finding(
                rule_id="misra-c2012-11.6",
                file="src/a.c",
                line=5,
                column=3,
                category=Category.REQUIRED,
                message="cast",
            ),
            _finding(
                rule_id="misra-c2012-15.5",
                file="src/b.c",
                line=9,
                category=Category.ADVISORY,
                message="exit",
            ),
        ]
    )
    out = tmp_path / "r.sarif"
    render_sarif(report, out)
    doc = json.loads(out.read_text())

    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "misch"
    assert len(run["results"]) == 2
    r0 = run["results"][0]
    assert r0["ruleId"] == "misra-c2012-11.6"
    assert r0["level"] == "error"  # required -> error
    loc = r0["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/a.c"
    assert loc["region"] == {"startLine": 5, "startColumn": 3}
    # advisory maps to warning
    assert run["results"][1]["level"] == "warning"


def _report_with_line_100():
    from misch.report.model import Report

    return Report(
        findings=[
            _finding(
                rule_id="misra-c2012-14.4",
                file="a.c",
                line=100,
                column=12,
                category=Category.REQUIRED,
                message="controlling expr [x]",
            ),
        ]
    )


def test_terminal_does_not_emoji_substitute_line_numbers(capsys):
    from misch.db import ScopeCoverage
    from misch.report.renderers import render_terminal

    cov = ScopeCoverage(analysed=["a.c"], excluded=[], unattributed=[])
    render_terminal(_report_with_line_100(), cov, verbose=True)
    out = capsys.readouterr().out
    assert "a.c:100:12" in out  # not turned into the :100: emoji
    assert "💯" not in out


def test_terminal_verbose_gates_locations(capsys):
    from misch.db import ScopeCoverage
    from misch.report.renderers import render_terminal

    cov = ScopeCoverage(analysed=["a.c"], excluded=[], unattributed=[])
    report = _report_with_line_100()

    render_terminal(report, cov, verbose=False)
    quiet = capsys.readouterr().out
    assert "a.c:100" not in quiet  # no per-location listing by default
    assert "verbose" in quiet.lower()

    render_terminal(report, cov, verbose=True)
    loud = capsys.readouterr().out
    assert "a.c:100" in loud


def test_find_stale_cross_references_unsuppressed_findings():
    from misch.report.deviations import Deviation, find_stale

    devs = [
        # live: an 11.4 finding sits on the next line
        Deviation("inline", "suppress", ["misra-c2012-11.4"], "a.c", 10, "ok"),
        # stale: nothing of 15.5 near line 50
        Deviation("inline", "suppress", ["misra-c2012-15.5"], "a.c", 50, "ok"),
        # not line-checkable: left alone
        Deviation("project", "project", ["*"], "*/vendor/*", None, "ok"),
    ]
    findings = [_finding(rule_id="misra-c2012-11.4", file="a.c", line=11)]

    assert find_stale(devs, findings) == ["a.c:50"]


def _cfg(tmp_path: Path, scope, exclude) -> Config:
    cfgfile = tmp_path / "misra.toml"
    cfgfile.write_text("[project]\n")  # minimal; fields set directly below
    c = Config(
        project_root=tmp_path,
        scope=scope,
        exclude=exclude,
        db_source="existing",
        db_path=None,
        platform="unix64",
        defines=[],
        rule_texts=None,
        outputs=[{"format": "terminal"}],
        baseline_path=tmp_path / "misra-baseline.json",
        suppressions_path=None,
    )
    return c


def test_in_scope_exclude_wins(tmp_path: Path):
    c = _cfg(tmp_path, scope=["src/"], exclude=["src/generated/"])
    assert in_scope(c, "src/a.c")
    assert not in_scope(c, "src/generated/tbl.c")
    assert not in_scope(c, "tests/t.c")  # not in scope


def test_partition_findings_surfaces_unattributed_locations(tmp_path: Path):
    """Findings at in-tree locations matching neither scope nor exclude must be
    surfaced (headers never appear in the compile DB, so classify_files cannot
    gate them); excluded and outside-tree locations stay silently dropped."""
    from misch.db import partition_findings

    c = _cfg(tmp_path, scope=["src/"], exclude=["tests/"])
    scoped = _finding(file="src/a.c")
    excluded = _finding(file="tests/t.h")  # explicit boundary: silent
    header = _finding(file="include/api.h")  # forgotten public header
    system = _finding(file="/usr/include/string.h")  # toolchain: silent

    kept, unattributed = partition_findings(c, [scoped, excluded, header, system])
    assert kept == [scoped]
    assert unattributed == [header]


def test_scaffold_documents_deviations_and_round_trips(tmp_path: Path):
    from misch.config import load
    from misch.scaffold import ScaffoldParams, build_config

    text = build_config(ScaffoldParams())
    lines = [ln.strip() for ln in text.splitlines()]
    assert "[deviations]" in lines
    # The key is documented but commented out, so a fresh project points at no
    # suppressions file until one is authored.
    assert 'suppressions = "misra-deviations.txt"' not in lines
    assert '# suppressions = "misra-deviations.txt"' in lines

    cfgfile = tmp_path / "misra.toml"
    cfgfile.write_text(text)
    cfg = load(cfgfile)
    assert cfg.suppressions_path is None


def test_build_cmd_wires_project_suppressions(tmp_path: Path):
    from misch.engine.cppcheck import _build_cmd

    c = _cfg(tmp_path, scope=["src/"], exclude=[])
    supp = tmp_path / "misra-suppressions.txt"
    supp.write_text("misra-c2012-15.5\n")
    c.suppressions_path = supp

    db, addon = tmp_path / "cc.json", tmp_path / "misra.json"
    cmd = _build_cmd(c, db, addon, inline_suppr=True)
    assert f"--suppressions-list={supp}" in cmd
    assert "--inline-suppr" in cmd


def test_build_cmd_drops_project_suppressions_when_inline_off(tmp_path: Path):
    """The staleness pass (inline_suppr=False) must see suppressed findings, so
    neither inline comments nor the project file may be applied."""
    from misch.engine.cppcheck import _build_cmd

    c = _cfg(tmp_path, scope=["src/"], exclude=[])
    supp = tmp_path / "misra-suppressions.txt"
    supp.write_text("misra-c2012-15.5\n")
    c.suppressions_path = supp

    db, addon = tmp_path / "cc.json", tmp_path / "misra.json"
    cmd = _build_cmd(c, db, addon, inline_suppr=False)
    assert not any(a.startswith("--suppressions-list=") for a in cmd)
    assert "--inline-suppr" not in cmd


def test_build_cmd_no_suppressions_list_when_file_absent(tmp_path: Path):
    from misch.engine.cppcheck import _build_cmd

    c = _cfg(tmp_path, scope=["src/"], exclude=[])
    c.suppressions_path = tmp_path / "does-not-exist.txt"

    db, addon = tmp_path / "cc.json", tmp_path / "misra.json"
    cmd = _build_cmd(c, db, addon, inline_suppr=True)
    assert not any(a.startswith("--suppressions-list=") for a in cmd)


def test_baseline_roundtrip_and_diff(tmp_path: Path):
    from misch.report.baseline import diff, load_baseline, write_baseline

    base = [
        _finding(rule_id="misra-c2012-15.5", file="src/a.c", message="single exit"),
        _finding(rule_id="misra-c2012-8.7", file="src/b.c", message="one TU"),
    ]
    path = tmp_path / "baseline.json"
    assert write_baseline(path, base) == 2

    loaded = load_baseline(path)
    assert len(loaded) == 2

    # One baselined finding (moved lines), one brand-new, one baseline now fixed.
    current = [
        _finding(
            rule_id="misra-c2012-15.5", file="src/a.c", message="single exit", line=999
        ),
        _finding(rule_id="misra-c2012-12.1", file="src/c.c", message="precedence"),
    ]
    d = diff(current, loaded)
    assert [f.rule_id for f in d.new] == ["misra-c2012-12.1"]
    assert [f.rule_id for f in d.baselined] == ["misra-c2012-15.5"]
    assert len(d.fixed) == 1  # the 8.7 finding is gone


def test_baseline_counts_catch_duplicate(tmp_path: Path):
    # Two indistinguishable same-rule/same-file findings (empty symbol, generic
    # message). Baseline accepts one; adding a second must show 1 new.
    from misch.report.baseline import diff, load_baseline, write_baseline

    msg = "external linkage referenced in one TU"
    one = [_finding(rule_id="misra-c2012-8.7", file="src/a.c", message=msg)]
    path = tmp_path / "b.json"
    write_baseline(path, one)
    base = load_baseline(path)

    two = [
        _finding(rule_id="misra-c2012-8.7", file="src/a.c", message=msg, line=10),
        _finding(rule_id="misra-c2012-8.7", file="src/a.c", message=msg, line=80),
    ]
    d = diff(two, base)
    assert len(d.new) == 1
    assert len(d.baselined) == 1


def test_baseline_ignores_non_misra(tmp_path: Path):
    from misch.report.baseline import write_baseline
    from misch.report.model import Source

    findings = [
        _finding(rule_id="nullPointer", source=Source.CPPCHECK),
        _finding(rule_id="misra-c2012-11.4"),
    ]
    assert write_baseline(tmp_path / "b.json", findings) == 1


def test_classify_flags_unattributed(tmp_path: Path):
    import json

    db = [
        {"file": f"{tmp_path}/src/a.c", "directory": str(tmp_path), "command": "cc"},
        {"file": f"{tmp_path}/weird/x.c", "directory": str(tmp_path), "command": "cc"},
    ]
    (tmp_path / "compile_commands.json").write_text(json.dumps(db))
    c = _cfg(tmp_path, scope=["src/"], exclude=["tests/"])
    cov = classify_files(c, tmp_path / "compile_commands.json")
    assert cov.analysed == ["src/a.c"]
    assert cov.unattributed == ["weird/x.c"]
    assert not cov.ok()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_missing_config_exits_2(tmp_path: Path, capsys):
    assert main(["run", "-c", str(tmp_path / "nope.toml")]) == 2
    assert "config error" in capsys.readouterr().err


def test_config_rejects_bad_db_source(tmp_path: Path):
    bad = tmp_path / "misra.toml"
    bad.write_text('[db]\nsource = "scons"\n')
    with pytest.raises(ConfigError):
        load(bad)


def test_config_rejects_bad_output_entry(tmp_path: Path):
    bad = tmp_path / "misra.toml"
    bad.write_text("[report]\noutputs = [42]\n")
    with pytest.raises(ConfigError):
        load(bad)


def test_resolve_compile_db_missing_existing_file(tmp_path: Path):
    c = _cfg(tmp_path, scope=["src/"], exclude=[])
    with pytest.raises(DbError, match="does not exist"):
        resolve_compile_db(c)


def test_init_writes_loadable_config_and_respects_force(tmp_path: Path):
    out = tmp_path / "misra.toml"
    assert main(["init", "-o", str(out)]) == 0
    cfg = load(out)  # the generated template must load cleanly
    assert cfg.db_source == "meson"
    assert cfg.scope == ["src/"]

    first = out.read_text()
    assert main(["init", "-o", str(out)]) == 2  # refuses to overwrite
    assert out.read_text() == first

    rc = main(["init", "-o", str(out), "--force", "--db", "existing"])
    assert rc == 0
    assert load(out).db_source == "existing"


def test_deviations_cli_writes_markdown_record(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text(
        "/* cppcheck-suppress[misra-c2012-15.5] ; @deviation guard clauses"
        " are house style */\n"
        "int f(void);\n"
    )
    cfg = tmp_path / "misra.toml"
    cfg.write_text('[project]\nscope = ["src/"]\n')

    md = tmp_path / "dev.md"
    rc = main(["deviations", "-c", str(cfg), "--format", "md", "--output", str(md)])
    assert rc == 0
    text = md.read_text()
    assert "misra-c2012-15.5" in text
    assert "guard clauses are house style" in text
    assert "`src/a.c:1`" in text


def test_deviations_cli_unjustified_fails(tmp_path: Path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text(
        "/* cppcheck-suppress[misra-c2012-15.5] */\nint f(void);\n"
    )
    cfg = tmp_path / "misra.toml"
    cfg.write_text('[project]\nscope = ["src/"]\n')

    assert main(["deviations", "-c", str(cfg)]) == 1
    assert "lack a justification" in capsys.readouterr().out


def test_deviation_markdown_record_golden(tmp_path: Path):
    record = DeviationRecord(
        deviations=[
            Deviation(
                origin="inline",
                kind="suppress",
                rule_ids=["misra-c2012-11.4"],
                file="src/a.c",
                line=10,
                justification="fixed device address",
            ),
            Deviation(
                origin="project",
                kind="project",
                rule_ids=["misra-c2012-15.5"],
                file="",
                line=None,
                justification="house style",
            ),
        ],
        stale=["src/b.c:7"],
    )
    out = tmp_path / "deviations.md"
    render_markdown(record, out)
    assert out.read_text() == (
        "# MISRA C:2023 deviation record\n"
        "\n"
        "Generated by `misch deviations`. Each entry is a suppressed "
        "guideline that must carry a documented rationale.\n"
        "\n"
        "## Inline deviations\n"
        "\n"
        "| Rule(s) | Location | Justification | Status |\n"
        "| --- | --- | --- | --- |\n"
        "| misra-c2012-11.4 | `src/a.c:10` | fixed device address | ok |\n"
        "\n"
        "## Project-level deviations\n"
        "\n"
        "| Rule(s) | Location | Justification | Status |\n"
        "| --- | --- | --- | --- |\n"
        "| misra-c2012-15.5 | `(global)` | house style | ok |\n"
        "\n"
        "## Stale suppressions\n"
        "\n"
        "These no longer match any finding and should be removed:\n"
        "\n"
        "- `src/b.c:7`\n"
        "\n"
    )


def test_render_baseline_summary_lists_new_and_fixed(tmp_path: Path, capsys):
    d = BaselineDiff(
        new=[_finding(line=5)],
        baselined=[_finding(line=1)],
        fixed=[
            {
                "rule_id": "misra-c2012-2.7",
                "file": "src/a.c",
                "message": "m",
                "count": 1,
            }
        ],
    )
    render_baseline_summary(d, tmp_path / "misra-baseline.json")
    out = capsys.readouterr().out
    assert "1 new" in out
    assert "1 baselined" in out
    assert "1 fixed" in out
    assert "src/a.c:5" in out
