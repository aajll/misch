"""End-to-end tests that drive the real cppcheck engine through the CLI.

These need cppcheck (plus its bundled `misra.py` addon) on PATH. They are
skipped when it is absent, so the unit suite still runs anywhere; CI installs
cppcheck so they execute there. No MISRA rule-texts are supplied (they are
copyrighted and not vendored), so findings carry `category: unknown`; the
assertions key off rule ids and counts, which the addon emits regardless.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from misch.cli import main

pytestmark = pytest.mark.skipif(
    shutil.which("cppcheck") is None, reason="cppcheck not on PATH"
)


def _write_project(root: Path, *, with_header: bool = False) -> Path:
    """Write a minimal C project with a real MISRA violation and an `existing`
    compile DB. Returns the misra.toml path.

    With `with_header`, the source pulls in include/helper.h, which carries its
    own violation — a header the compile DB never lists, exercising the
    unattributed-finding-location gate.
    """
    src = root / "src"
    src.mkdir()
    c = src / "sample.c"
    # Two return statements -> MISRA C:2012 Rule 15.5 (single point of exit),
    # which the cppcheck misra addon flags reliably across versions.
    body = (
        "unsigned int pick(unsigned int x)\n"
        "{\n"
        "    if (x > 10u) {\n"
        "        return 1u;\n"
        "    }\n"
        "    return 0u;\n"
        "}\n"
    )
    args = ["cc", "-c", str(c)]
    if with_header:
        inc = root / "include"
        inc.mkdir()
        (inc / "helper.h").write_text(
            "static unsigned int clamp10(unsigned int x)\n"
            "{\n"
            "    if (x > 10u) {\n"
            "        return 10u;\n"
            "    }\n"
            "    return x;\n"
            "}\n"
        )
        body = '#include "helper.h"\n\n' + body
        args.insert(1, f"-I{inc}")
    c.write_text(body)
    (root / "compile_commands.json").write_text(
        json.dumps([{"directory": str(src), "file": str(c), "arguments": args}])
    )
    cfg = root / "misra.toml"
    cfg.write_text(
        "[project]\n"
        'scope = ["src/"]\n'
        "exclude = []\n"
        "[db]\n"
        'source = "existing"\n'
        'path = "compile_commands.json"\n'
        "[platform]\n"
        'preset = "unix64"\n'
    )
    return cfg


def _run_json(cfg: Path, out: Path) -> dict:
    rc = main(["run", "-c", str(cfg), "--format", "json", "--output", str(out)])
    return {"rc": rc, "doc": json.loads(out.read_text())}


def test_e2e_run_detects_violation(tmp_path: Path):
    cfg = _write_project(tmp_path)
    result = _run_json(cfg, tmp_path / "out.json")

    assert result["rc"] == 1  # findings -> non-zero exit
    doc = result["doc"]
    ids = {f["rule_id"] for f in doc["findings"]}
    assert "misra-c2012-15.5" in ids
    assert doc["summary"]["misra_findings"] >= 1


def test_e2e_profile_flag_applies_preprocessor_configuration(tmp_path: Path):
    """A selected profile changes cppcheck's active preprocessor branch."""
    cfg = _write_project(tmp_path)
    source = tmp_path / "src" / "sample.c"
    source.write_text(
        "#ifdef ARM_DEFINE\n"
        "static unsigned int pick(unsigned int x)\n"
        "{\n"
        "    if (x > 10u) {\n"
        "        return 1u;\n"
        "    }\n"
        "    return 0u;\n"
        "}\n"
        "#else\n"
        "static unsigned int pick(unsigned int x)\n"
        "{\n"
        "    return x;\n"
        "}\n"
        "#endif\n"
    )
    cfg.write_text(
        cfg.read_text() + "\n[profiles.arm]\n" + 'toolchain.defines = ["ARM_DEFINE"]\n'
    )

    base = _run_json(cfg, tmp_path / "base.json")
    assert base["rc"] == 0
    assert base["doc"]["summary"]["misra_findings"] == 0

    profile_out = tmp_path / "arm.json"
    profile_rc = main(
        [
            "run",
            "-c",
            str(cfg),
            "--profile",
            "arm",
            "--format",
            "json",
            "--output",
            str(profile_out),
        ]
    )
    assert profile_rc == 1
    profile_doc = json.loads(profile_out.read_text())
    profile_ids = {finding["rule_id"] for finding in profile_doc["findings"]}
    assert "misra-c2012-15.5" in profile_ids


def test_e2e_unattributed_header_findings_are_a_hard_error(tmp_path: Path, capsys):
    """A header pulled in from a scoped source, living in a directory that is
    neither scoped nor excluded, must fail the run — not silently vanish."""
    cfg = _write_project(tmp_path, with_header=True)

    rc = main(["run", "-c", str(cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "scope error" in err
    assert "helper.h" in err

    # Classifying the directory (scope it) turns the same findings into an
    # ordinary reported result.
    cfg.write_text(cfg.read_text().replace('["src/"]', '["src/", "include/"]'))
    out = tmp_path / "out.json"
    result = _run_json(cfg, out)
    assert result["rc"] == 1
    files = {f["file"] for f in result["doc"]["findings"]}
    assert "include/helper.h" in files


def test_e2e_baseline_ratchet(tmp_path: Path):
    """The flagship flow: accept current findings, then fail only on new ones."""
    cfg = _write_project(tmp_path)

    assert main(["baseline", "-c", str(cfg)]) == 0
    assert (tmp_path / "misra-baseline.json").is_file()

    # Unchanged code: the ratchet passes even though the findings persist.
    assert main(["run", "-c", str(cfg), "--baseline"]) == 0

    # A fresh violation (new function, so a distinct fingerprint) must fail it.
    c = tmp_path / "src" / "sample.c"
    c.write_text(
        c.read_text() + "\nunsigned int pick2(unsigned int x)\n"
        "{\n"
        "    if (x > 20u) {\n"
        "        return 2u;\n"
        "    }\n"
        "    return 0u;\n"
        "}\n"
    )
    assert main(["run", "-c", str(cfg), "--baseline"]) == 1


def test_e2e_check_stale_flags_dead_suppression(tmp_path: Path, capsys):
    """--check-stale must flag a suppression whose rule never fires at its
    site, and leave a live suppression alone."""
    cfg = _write_project(tmp_path)
    c = tmp_path / "src" / "sample.c"
    lines = c.read_text().splitlines()
    # Dead: nothing in this file violates 21.6 (no stdio use at line 1/2).
    lines.insert(0, "/* cppcheck-suppress[misra-c2012-21.6] ; @deviation dead entry */")
    # Live: 15.5 fires at the early return; a suppression on the line above
    # it is within the line/line+1 window find_stale treats as live.
    early = next(i for i, ln in enumerate(lines) if "return 1u;" in ln)
    lines.insert(
        early, "    /* cppcheck-suppress[misra-c2012-15.5] ; @deviation live */"
    )
    c.write_text("\n".join(lines) + "\n")

    assert main(["deviations", "-c", str(cfg), "--check-stale"]) == 0
    out = capsys.readouterr().out
    assert "stale suppression" in out
    # Stale entries are rendered as "~ file:line"; both deviations also appear
    # in the plain listing above, so match on the stale marker specifically.
    assert "~ src/sample.c:1" in out  # the dead 21.6 entry
    assert f"~ src/sample.c:{early + 1}" not in out  # the live 15.5 entry


def test_e2e_project_suppressions_silence_findings(tmp_path: Path):
    """Guards the `[deviations].suppressions` -> cppcheck --suppressions-list
    wiring: a project deviation must actually silence the analysis, not merely
    be harvested by `misch deviations`."""
    cfg = _write_project(tmp_path)
    before = _run_json(cfg, tmp_path / "before.json")
    ids = sorted({f["rule_id"] for f in before["doc"]["findings"]})
    assert ids  # sanity: the baseline run found something to suppress

    dev = tmp_path / "misra-deviations.txt"
    lines = ["# e2e: blanket-deviate every rule the baseline run reported"]
    lines += ids
    dev.write_text("\n".join(lines) + "\n")
    cfg.write_text(
        cfg.read_text() + '\n[deviations]\nsuppressions = "misra-deviations.txt"\n'
    )

    after = _run_json(cfg, tmp_path / "after.json")
    assert after["doc"]["summary"]["misra_findings"] == 0
    assert after["rc"] == 0
