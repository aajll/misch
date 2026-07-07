"""Compile-database resolution and scope classification.

`compile_commands.json` is the universal seam: every project-specific build
concern collapses into "produce a normalised compile DB". This module obtains
that DB (from an existing file, or by configuring Meson/CMake) and classifies
every file it references as analysed / excluded / unattributed.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Config


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
    files = sorted({_rel(cfg.project_root, e["file"]) for e in entries})

    analysed, excluded, unattributed = [], [], []
    for f in files:
        if _matches_any(f, cfg.exclude):
            excluded.append(f)
        elif not cfg.scope or _matches_any(f, cfg.scope):
            analysed.append(f)
        else:
            unattributed.append(f)
    return ScopeCoverage(analysed, excluded, unattributed)


def _rel(root: Path, file: str) -> str:
    p = Path(file)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()  # outside the tree (system/toolchain header)


def _matches_any(path: str, globs: list[str]) -> bool:
    for g in globs:
        g = g.strip()
        if not g:
            continue
        base = g.rstrip("/")
        # Directory-style pattern: match anything beneath it.
        if path == base or path.startswith(base + "/"):
            return True
        if fnmatch.fnmatch(path, g):
            return True
    return False


def in_scope(cfg: Config, path: str) -> bool:
    """True if a finding at `path` should be reported (in scope, not excluded)."""
    if _matches_any(path, cfg.exclude):
        return False
    return not cfg.scope or _matches_any(path, cfg.scope)


def partition_findings(cfg: Config, findings: list) -> tuple[list, list]:
    """Split findings into (kept, unattributed-location).

    The compile DB only lists translation units, so `classify_files` cannot see
    headers: they enter the analysis via inclusion, and cppcheck reports
    findings at their locations. A finding inside the project tree whose path
    matches neither `scope` nor `exclude` is therefore unclassified audit
    territory and must be surfaced, not silently dropped. Findings at
    explicitly excluded paths and outside the tree (system/toolchain headers;
    `_rel` leaves those absolute) are dropped silently by design.
    """
    kept, unattributed = [], []
    for f in findings:
        if not f.file:
            continue
        if in_scope(cfg, f.file):
            kept.append(f)
        elif not Path(f.file).is_absolute() and not _matches_any(f.file, cfg.exclude):
            unattributed.append(f)
    return kept, unattributed
