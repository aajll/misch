"""Tests for the scope policy module's public interface."""

from __future__ import annotations

from pathlib import Path

from misch.scope_policy import (
    ScopeClass,
    classify,
    classify_one,
    is_external,
    select_sources,
    to_project_rel,
)

# ---------------------------------------------------------------------------
# classify — classification logic
# ---------------------------------------------------------------------------


def test_classify_empty_scope_means_all_in():
    """When scope is empty every path is in-scope (no exclusion)."""
    buckets = classify(["src/a.c", "lib/b.h"], [], [])
    assert set(buckets[ScopeClass.IN_SCOPE]) == {"src/a.c", "lib/b.h"}


def test_classify_exclude_wins_over_scope():
    buckets = classify(
        ["src/a.c", "src/gen/tbl.c"], scope=["src/"], exclude=["src/gen/"]
    )
    assert set(buckets[ScopeClass.IN_SCOPE]) == {"src/a.c"}
    assert set(buckets[ScopeClass.EXCLUDED]) == {"src/gen/tbl.c"}


def test_classify_unattributed_when_no_match():
    buckets = classify(["tests/t.c"], scope=["src/"], exclude=[])
    assert set(buckets[ScopeClass.UNATTRIBUTED]) == {"tests/t.c"}


def test_classify_empty_input_returns_empty_buckets():
    buckets = classify([], ["src/"], [])
    for cls in ScopeClass:
        assert buckets[cls] == []


# ---------------------------------------------------------------------------
# classify_one — glob semantics
# ---------------------------------------------------------------------------


def test_matches_directory_boundary():
    assert classify_one("src/a.c", ["src/"], []) is ScopeClass.IN_SCOPE
    assert classify_one("src/sub/a.c", ["src/"], []) is ScopeClass.IN_SCOPE
    # A prefix match must land on a directory boundary.
    assert classify_one("srcextra/a.c", ["src/"], []) is ScopeClass.UNATTRIBUTED


def test_matches_fnmatch_glob():
    assert classify_one("x/gen.h", ["*.h"], []) is ScopeClass.IN_SCOPE
    assert classify_one("x/gen.c", ["*.h"], []) is ScopeClass.UNATTRIBUTED


def test_matches_empty_glob_ignored():
    """An empty entry matches nothing, rather than standing for everything."""
    assert classify_one("anything.c", [""], []) is ScopeClass.UNATTRIBUTED


def test_classify_one_exclude_wins():
    assert classify_one("src/gen/tbl.c", ["src/"], ["src/gen/"]) is ScopeClass.EXCLUDED


# ---------------------------------------------------------------------------
# to_project_rel — path normalization
# ---------------------------------------------------------------------------


def test_to_project_rel_inside_tree(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "src").mkdir()
    file = root / "src" / "a.c"
    file.touch()
    rel = to_project_rel(root, file)
    assert rel == "src/a.c"


def test_to_project_rel_outside_tree(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    external = tmp_path / "other" / "lib.c"
    external.parent.mkdir(exist_ok=True)
    external.touch()
    rel = to_project_rel(root, external)
    # Falls back to absolute posix path.
    assert Path(rel).is_absolute()


# ---------------------------------------------------------------------------
# select_sources — source walk + filter
# ---------------------------------------------------------------------------


def test_select_sources_basic(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.c").write_text("")
    (root / "src" / "util.h").write_text("")
    (root / "src" / "readme.md").write_text("")  # ignored

    sources = select_sources(root, scope=["src"], exclude=[])
    assert len(sources) == 2
    assert all(p.suffix in {".c", ".h"} for p in sources)


def test_select_sources_excludes(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "gen").mkdir()
    (root / "src" / "a.c").write_text("")
    (root / "gen" / "tbl.c").write_text("")

    sources = select_sources(root, scope=[], exclude=["gen/"])
    assert len(sources) == 1
    assert sources[0].name == "a.c"


def test_select_sources_glob_scope_entry(tmp_path: Path):
    """A non-directory scope entry is expanded as a glob against the root."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.c").write_text("")
    (root / "src" / "util.h").write_text("")

    sources = select_sources(root, scope=["src/*.c"], exclude=[])
    assert [p.name for p in sources] == ["main.c"]


def test_select_sources_file_scope_entry(tmp_path: Path):
    """A scope entry naming a single file selects exactly that file."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.c").write_text("")
    (root / "src" / "other.c").write_text("")

    sources = select_sources(root, scope=["src/main.c"], exclude=[])
    assert [p.name for p in sources] == ["main.c"]


def test_select_sources_applies_scope_match(tmp_path: Path):
    """A walk from one scope root admits only files some scope entry matches."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "src" / "a.c").write_text("")
    (root / "lib" / "b.c").write_text("")

    # "." walks the whole tree, but lib/b.c matches no scope entry.
    sources = select_sources(root, scope=["src", "."], exclude=[])
    assert [p.name for p in sources] == ["a.c"]


def test_select_sources_deduplicates(tmp_path: Path):
    """Overlapping scope roots don't produce duplicates."""
    root = tmp_path / "proj"
    (root / "lib" / "sub").mkdir(parents=True)
    (root / "lib" / "a.c").write_text("")
    (root / "lib" / "sub" / "b.c").write_text("")

    # Both "lib/" and "." cover the same files.
    sources = select_sources(root, scope=["lib/", "."], exclude=[])
    assert len(sources) == 2


def test_select_sources_sorted(tmp_path: Path):
    """Results are deterministically sorted regardless of discovery order."""
    root = tmp_path / "proj"
    (root / "b").mkdir(parents=True)
    (root / "a").mkdir()
    (root / "b" / "z.c").write_text("")
    (root / "a" / "m.c").write_text("")
    (root / "a" / "k.h").write_text("")

    sources = select_sources(root, scope=["b", "a"], exclude=[])
    assert sources == sorted(sources)
    assert [p.name for p in sources] == ["k.h", "m.c", "z.c"]


# ---------------------------------------------------------------------------
# is_external — containment in the project tree
# ---------------------------------------------------------------------------


def test_is_external_outside_tree(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    assert is_external(root, "/usr/include/stddef.h") is True


def test_is_external_project_relative(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    assert is_external(root, "src/main.c") is False


def test_is_external_absolute_inside_tree(tmp_path: Path):
    """An absolute path under the root is in the project, not external."""
    root = tmp_path / "proj"
    root.mkdir()
    assert is_external(root, root / "src" / "a.c") is False
