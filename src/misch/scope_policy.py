"""Project-scope matching, classification, and source selection.

Owns the shared policy exercised by three adapters:

- compile-DB file classification (db module)
- finding-location partitioning (db module)
- deviation source discovery (report/deviations module)

Glob precedence, project-relative path rules, and external-location detection
live here. Callers import only the public symbols; matching semantics change
and are verified in one place.

Deletion test: removing this module would scatter inclusion/exclusion
precedence and project-relative path logic across all three callers.
"""

from __future__ import annotations

import fnmatch
from enum import Enum
from pathlib import Path


class ScopeClass(Enum):
    """Classification of a project-relative path against scope / exclude globs."""

    IN_SCOPE = "in_scope"
    EXCLUDED = "excluded"
    UNATTRIBUTED = "unattributed"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _matches_any(path: str, globs: list[str]) -> bool:
    """Return True if *path* matches any entry in *globs*.

    Directory-style entries (e.g. ``"src/"``) match everything beneath the
    directory.  Plain globs (e.g. ``"*.c"``) are matched with fnmatch.
    """
    for g in globs:
        g = g.strip()
        if not g:
            continue
        base = g.rstrip("/")
        if path == base or path.startswith(base + "/"):
            return True
        if fnmatch.fnmatch(path, g):
            return True
    return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def to_project_rel(root: Path, path: str | Path) -> str:
    """Resolve *path* relative to *root*, or return as-is if external."""
    p = Path(path).resolve()
    try:
        return p.relative_to(root.resolve()).as_posix()
    except ValueError:
        # Outside the project tree (system / toolchain header).
        return p.as_posix()


def classify(
    paths: list[str],
    scope: list[str],
    exclude: list[str],
) -> dict[ScopeClass, list[str]]:
    """Bucket *paths* into in-scope / excluded / unattributed.

    Exclude globs win over scope globs.  A path that matches neither is
    unattributed; the caller decides whether to raise or skip.

    All paths must already be project-relative posix strings (use
    ``to_project_rel`` to normalize before calling).
    """
    result = {cls: [] for cls in ScopeClass}
    for p in paths:
        if _matches_any(p, exclude):
            result[ScopeClass.EXCLUDED].append(p)
        elif not scope or _matches_any(p, scope):
            result[ScopeClass.IN_SCOPE].append(p)
        else:
            result[ScopeClass.UNATTRIBUTED].append(p)
    return result


def select_sources(
    root: Path,
    scope: list[str],
    exclude: list[str],
) -> list[Path]:
    """Walk *root* and return sorted in-scope C source paths.

    Scans for ``.c``, ``.h``, ``.cpp``, ``.hpp``, ``.cc`` files beneath each
    directory scope entry; a non-directory entry (e.g. ``"src/*.c"``) is
    expanded as a glob relative to *root*.  Candidates then pass the same
    exclude-over-scope precedence as ``classify``, and only in-scope results
    are returned as absolute Paths.  Used by deviation harvesting to run
    without a compile database.
    """
    exts = {".c", ".h", ".cpp", ".hpp", ".cc"}
    roots = scope or ["."]
    resolved_root = root.resolve()
    seen: set[Path] = set()
    out: list[Path] = []

    for entry in roots:
        base = root / entry.rstrip("/")
        candidates = base.rglob("*") if base.is_dir() else root.glob(entry)
        for p in candidates:
            if p.suffix not in exts or not p.is_file() or p in seen:
                continue
            rel = to_project_rel(resolved_root, p)
            if _matches_any(rel, exclude):
                continue
            if scope and not _matches_any(rel, scope):
                continue
            seen.add(p)
            out.append(p)

    return sorted(out)


def is_external(path: str | Path) -> bool:
    """Return True if *path* resolves to an absolute location outside a project.

    Used by finding partitioning to silently omit system / toolchain headers
    that the compile database never listed but cppcheck still reports against.
    """
    return Path(path).is_absolute()
