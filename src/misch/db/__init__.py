"""Compile-database resolution and scope classification.

`compile_commands.json` is the universal seam: every project-specific build
concern collapses into "produce a normalised compile DB". This module obtains
that DB (from an existing file, or by configuring Meson/CMake) and classifies
every file it references as analysed / excluded / unattributed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..scope_policy import (
    ScopeClass,
    classify,
    classify_one,
    is_external,
    to_project_rel,
)


class DbError(Exception):
    pass


def resolve_compile_db(cfg: Config) -> Path:
    """Return a *normalised* compile_commands.json for the configured source.

    Normalisation rewrites every entry's `file` to an absolute path (resolved
    against its `directory`), so downstream scope classification and the engine
    see uniform paths regardless of whether the build system emitted relative or
    absolute ones.
    """
    if cfg.db_source == "existing":
        rel = cfg.db_path or "build/compile_commands.json"
        raw = (cfg.project_root / rel).resolve()
        if not raw.is_file():
            raise DbError(f"db.source=existing but {raw} does not exist")
    elif cfg.db_source == "meson":
        raw = _meson_db(cfg)
    elif cfg.db_source == "cmake":
        raw = _cmake_db(cfg)
    else:
        raise DbError(f"unsupported db.source: {cfg.db_source!r}")

    return _normalise_db(raw, cfg.build_dir)


def _normalise_db(raw: Path, out_dir: Path) -> Path:
    entries = json.loads(raw.read_text())
    for e in entries:
        f = Path(e["file"])
        if not f.is_absolute():
            f = Path(e.get("directory", ".")) / f
        e["file"] = str(f.resolve())
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "compile_commands.normalised.json"
    out.write_text(json.dumps(entries))
    return out


def _meson_db(cfg: Config) -> Path:
    db = cfg.build_dir / "compile_commands.json"
    if not db.is_file():
        _require("meson")
        subprocess.run(
            ["meson", "setup", str(cfg.build_dir), str(cfg.project_root)],
            check=True,
        )
    if not db.is_file():
        raise DbError(f"meson did not produce {db}")
    return db


def _cmake_db(cfg: Config) -> Path:
    db = cfg.build_dir / "compile_commands.json"
    if not db.is_file():
        _require("cmake")
        subprocess.run(
            [
                "cmake",
                "-S",
                str(cfg.project_root),
                "-B",
                str(cfg.build_dir),
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            ],
            check=True,
        )
    if not db.is_file():
        raise DbError(f"cmake did not produce {db}")
    return db


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise DbError(f"{tool} not found on PATH (needed for db.source)")


@dataclass(slots=True)
class ScopeCoverage:
    analysed: list[str]
    excluded: list[str]
    unattributed: list[str]

    def ok(self) -> bool:
        return not self.unattributed


def classify_files(cfg: Config, db_path: Path) -> ScopeCoverage:
    """Bucket every DB file into analysed / excluded / unattributed.

    Enforces the "exclusion is explicit and enumerated" rule: a file that
    matches neither `scope` nor `exclude` is unattributed, which is a hard
    error so nothing is ever silently ignored.
    """
    entries = json.loads(db_path.read_text())
    files = sorted({to_project_rel(cfg.project_root, e["file"]) for e in entries})
    buckets = classify(files, cfg.scope, cfg.exclude)
    return ScopeCoverage(
        analysed=buckets[ScopeClass.IN_SCOPE],
        excluded=buckets[ScopeClass.EXCLUDED],
        unattributed=buckets[ScopeClass.UNATTRIBUTED],
    )


def partition_findings(cfg: Config, findings: list) -> tuple[list, list]:
    """Split findings into (kept, unattributed-location).

    The compile DB only lists translation units, so `classify_files` cannot see
    headers: they enter the analysis via inclusion, and cppcheck reports
    findings at their locations. A finding inside the project tree whose path
    matches neither `scope` nor `exclude` is therefore unclassified audit
    territory and must be surfaced, not silently dropped. Findings at
    explicitly excluded paths are dropped silently, as are findings outside
    the tree (system/toolchain headers, reported at absolute paths) that fall
    outside a configured scope; with an empty scope every non-excluded finding
    is kept.
    """
    kept, unattributed = [], []
    for f in findings:
        if not f.file:
            continue
        cls = classify_one(f.file, cfg.scope, cfg.exclude)
        if cls is ScopeClass.IN_SCOPE:
            kept.append(f)
        elif cls is ScopeClass.UNATTRIBUTED and not is_external(
            cfg.project_root, f.file
        ):
            unattributed.append(f)
        # else: excluded, or unattributed outside the tree; dropped silently.
    return kept, unattributed
