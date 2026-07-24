"""Project-scope matching, classification, and source selection.

Owns the shared policy exercised by the scope adapters:

- compile-DB file classification (db module)
- finding-location partitioning (db module)
- deviation source discovery (report/deviations module)

Glob precedence, external-location detection, and project-relative path rules
live here. Normalization is also what the finding parser and the deviation
harvester call before any of the above sees a path, so producers and consumers
of a project-relative path share one definition of it. Callers import only the
public symbols, so matching semantics change and are verified in one place.
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
    """Resolve *path* relative to *root*.

    A path outside the tree (system / toolchain header) has no project-relative
    form and comes back as an absolute posix string.  An empty *path* stays
    empty, because resolving one would silently yield the working directory.
    """
    if not path:
        return ""
    p = Path(path).resolve()
    try:
        return p.relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def classify_one(path: str, scope: list[str], exclude: list[str]) -> ScopeClass:
    """Classify one project-relative path against the scope / exclude globs.

    Exclude globs win over scope globs, and an empty scope puts everything not
    excluded in scope.  A path matching neither is unattributed; the caller
    decides whether to raise or skip.
    """
    if _matches_any(path, exclude):
        return ScopeClass.EXCLUDED
    if not scope or _matches_any(path, scope):
        return ScopeClass.IN_SCOPE
    return ScopeClass.UNATTRIBUTED


def classify(
    paths: list[str],
    scope: list[str],
    exclude: list[str],
) -> dict[ScopeClass, list[str]]:
    """Bucket *paths* by ``classify_one``.

    All paths must already be project-relative posix strings (use
    ``to_project_rel`` to normalize before calling).
    """
    result = {cls: [] for cls in ScopeClass}
    for p in paths:
        result[classify_one(p, scope, exclude)].append(p)
    return result


def select_sources(
    root: Path,
    scope: list[str],
    exclude: list[str],
) -> list[Path]:
    """Walk *root* and return sorted in-scope C source paths.

    Scans for ``.c``, ``.h``, ``.cpp``, ``.hpp``, ``.cc`` files beneath each
    directory scope entry; a non-directory entry (e.g. ``"src/*.c"``) is
    expanded as a glob relative to *root*.  Every candidate is then admitted by
    ``classify_one``, so the same precedence applies here as everywhere else,
    and survivors are returned as absolute Paths.  Used by deviation harvesting
    to run without a compile database.
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
            if classify_one(rel, scope, exclude) is not ScopeClass.IN_SCOPE:
                continue
            seen.add(p)
            out.append(p)

    return sorted(out)


def is_external(root: Path, path: str | Path) -> bool:
    """Return True if *path* lies outside the *root* project tree.

    Used by finding partitioning to silently omit system / toolchain headers
    that the compile database never listed but cppcheck still reports against.
    Both project-relative and absolute paths are accepted: joining an absolute
    path to *root* leaves it unchanged.
    """
    return not (root / path).resolve().is_relative_to(root.resolve())
