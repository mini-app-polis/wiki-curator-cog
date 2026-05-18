"""Tests for index.md and log.md helpers."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from wiki_curator_cog.wiki_files import (
    append_log_entry,
    format_source_index_line,
    regenerate_index_derived_sections,
    remove_source_from_index,
    upsert_source_in_index,
)

_INDEX_SKELETON = """# Index

Catalog of all pages in this wiki.

---

## Concepts

_No concept pages yet. They will be created as sources are ingested._

## Techniques

_No technique pages yet. They will be created as sources are ingested._

## Instructors

_No instructor pages yet. They will be created as sources are ingested._

## Terminology

_No terminology pages yet. They will be created when vocabulary reconciliation is needed._

## Views

_No view pages yet._

## Sources

_No sources ingested yet._
"""


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    p = tmp_path / "index.md"
    p.write_text(_INDEX_SKELETON)
    return p


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    p = tmp_path / "log.md"
    p.write_text(
        "# Log\n\n"
        "## [2026-05-16] schema-update | initial repo creation\n\n"
        "Initial commit.\n"
    )
    return p


# ── format_source_index_line ────────────────────────────────────────────


def test_format_source_index_line_with_title() -> None:
    line = format_source_index_line(
        source_slug="2025-09-15-anchor-step-quality",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title="Anchor step quality",
    )
    assert "[[sources/kate/2025-09-15-anchor-step-quality]]" in line
    assert "2025-09-15" in line
    assert "Kate" in line
    assert "private lesson" in line
    assert '"Anchor step quality"' in line


def test_format_source_index_line_without_title() -> None:
    line = format_source_index_line(
        source_slug="2025-09-15-kate-private-lesson",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    assert "[[sources/kate/2025-09-15-kate-private-lesson]]" in line
    assert '"' not in line  # no quoted title


def test_format_source_index_line_multi_instructor() -> None:
    line = format_source_index_line(
        source_slug="2025-09-15-workshop",
        bucket="kate",
        canonical_instructors=["kate", "robert"],
        session_type="group_class",
        session_date=dt.date(2025, 9, 15),
        title="Routine prep",
    )
    assert "Kate, Robert" in line


def test_format_source_index_line_hyphenated_instructor_titlecased() -> None:
    line = format_source_index_line(
        source_slug="2025-09-15-foo",
        bucket="external",
        canonical_instructors=["kaiano-levine"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    assert "Kaiano Levine" in line


# ── upsert_source_in_index ──────────────────────────────────────────────


def test_upsert_replaces_placeholder_on_first_insert(index_path: Path) -> None:
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-15-anchor-step-quality",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title="Anchor step quality",
    )
    text = index_path.read_text()
    assert "_No sources ingested yet._" not in text
    assert "[[sources/kate/2025-09-15-anchor-step-quality]]" in text


def test_upsert_sorts_newest_first(index_path: Path) -> None:
    for date, slug in [
        (dt.date(2025, 9, 15), "2025-09-15-anchor"),
        (dt.date(2025, 10, 1), "2025-10-01-frame"),
        (dt.date(2025, 7, 4), "2025-07-04-musicality"),
    ]:
        upsert_source_in_index(
            index_path,
            source_slug=slug,
            bucket="kate",
            canonical_instructors=["kate"],
            session_type="private_lesson",
            session_date=date,
            title=None,
        )
    text = index_path.read_text()
    # Newer (October) should appear before older (September) before
    # oldest (July) within the Sources section.
    oct_idx = text.index("2025-10-01-frame")
    sep_idx = text.index("2025-09-15-anchor")
    jul_idx = text.index("2025-07-04-musicality")
    assert oct_idx < sep_idx < jul_idx


def test_upsert_idempotent_on_same_slug(index_path: Path) -> None:
    args = dict(
        source_slug="2025-09-15-anchor-step",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title="Anchor step",
    )
    upsert_source_in_index(index_path, **args)  # type: ignore[arg-type]
    upsert_source_in_index(index_path, **args)  # type: ignore[arg-type]
    upsert_source_in_index(index_path, **args)  # type: ignore[arg-type]
    text = index_path.read_text()
    assert text.count("[[sources/kate/2025-09-15-anchor-step]]") == 1


def test_upsert_replaces_metadata_on_re_insert(index_path: Path) -> None:
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-15-foo",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    # Re-ingest with a new title (e.g., upstream re-extraction filled
    # the title field). The line should be replaced, not duplicated.
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-15-foo",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title="Anchor step quality",
    )
    text = index_path.read_text()
    assert text.count("2025-09-15-foo") == 1
    assert '"Anchor step quality"' in text


def test_upsert_preserves_other_sections(index_path: Path) -> None:
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-15-foo",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    text = index_path.read_text()
    # All other sections should be intact, including their placeholders.
    assert "## Concepts" in text
    assert "_No concept pages yet." in text
    assert "## Techniques" in text
    assert "## Views" in text


def test_upsert_creates_sources_section_if_missing(tmp_path: Path) -> None:
    p = tmp_path / "index.md"
    p.write_text("# Index\n\n## Concepts\n\n_None._\n")
    upsert_source_in_index(
        p,
        source_slug="2025-09-15-foo",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    text = p.read_text()
    assert "## Sources" in text
    assert "[[sources/kate/2025-09-15-foo]]" in text


# ── remove_source_from_index ────────────────────────────────────────────


def test_remove_source_from_index_removes_matching_line(index_path: Path) -> None:
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-15-foo",
        bucket="external",
        canonical_instructors=["foo-bar"],
        session_type="private_lesson",
        session_date=dt.date(2025, 9, 15),
        title=None,
    )
    upsert_source_in_index(
        index_path,
        source_slug="2025-09-16-bar",
        bucket="kate",
        canonical_instructors=["kate"],
        session_type="group_class",
        session_date=dt.date(2025, 9, 16),
        title=None,
    )
    assert (
        remove_source_from_index(
            index_path, source_slug="2025-09-15-foo", bucket="external"
        )
        is True
    )
    text = index_path.read_text()
    assert "[[sources/external/2025-09-15-foo]]" not in text
    # Other source untouched.
    assert "[[sources/kate/2025-09-16-bar]]" in text


def test_remove_source_from_index_no_op_when_missing(index_path: Path) -> None:
    assert (
        remove_source_from_index(index_path, source_slug="never-existed", bucket="kate")
        is False
    )
    # Index still has its placeholder.
    assert "_No sources ingested yet._" in index_path.read_text()


def test_remove_source_from_index_no_op_when_file_missing(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-such-index.md"
    assert (
        remove_source_from_index(nonexistent, source_slug="foo", bucket="kate") is False
    )


# ── append_log_entry ────────────────────────────────────────────────────


def test_append_log_entry_appends(log_path: Path) -> None:
    append_log_entry(
        log_path,
        action="ingest",
        subject="2025-09-15-anchor",
        body="Ingested source `2025-09-15-anchor`.",
        date=dt.date(2026, 5, 17),
    )
    text = log_path.read_text()
    assert "## [2026-05-17] ingest | 2025-09-15-anchor" in text
    assert "Ingested source `2025-09-15-anchor`." in text
    # Preserves the existing entry.
    assert "schema-update | initial repo creation" in text


def test_append_log_entry_creates_file_if_missing(tmp_path: Path) -> None:
    p = tmp_path / "log.md"
    append_log_entry(
        p,
        action="ingest",
        subject="2025-09-15-x",
        body="body",
        date=dt.date(2026, 5, 17),
    )
    text = p.read_text()
    assert text.startswith("# Log")
    assert "## [2026-05-17] ingest | 2025-09-15-x" in text


def test_append_log_entry_multiple_appends_in_order(log_path: Path) -> None:
    append_log_entry(
        log_path,
        action="ingest",
        subject="alpha",
        body="first",
        date=dt.date(2026, 5, 17),
    )
    append_log_entry(
        log_path,
        action="ingest",
        subject="beta",
        body="second",
        date=dt.date(2026, 5, 18),
    )
    text = log_path.read_text()
    alpha_idx = text.index("alpha")
    beta_idx = text.index("beta")
    assert alpha_idx < beta_idx


# ── regenerate_index_derived_sections ───────────────────────────────────


_INDEX_WITH_DERIVED_SKELETON = """# Index

Catalog of all pages in this wiki.

---

## Concepts

_No concept pages yet. They will be created as sources are ingested._

## Techniques

_No technique pages yet. They will be created as sources are ingested._

## Instructors

_No instructor pages yet. They will be created as sources are ingested._

## Terminology

_No terminology pages yet. They will be created when vocabulary reconciliation is needed._

## Views

- views/full-model.md

## Sources

_No sources ingested yet._
"""


def _seed_derived_page(
    wiki_repo_path: Path,
    *,
    subdir: str,
    slug: str,
    frontmatter: dict,
) -> Path:
    """Write a minimal derived page with the given frontmatter."""
    import yaml

    page_path = wiki_repo_path / subdir / f"{slug}.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False)
    page_path.write_text(f"---\n{fm_yaml}---\n\n## Overview\n\nx\n")
    return page_path


def test_regenerate_index_rewrites_empty_skeleton_sections(
    tmp_path: Path,
) -> None:
    """The skeleton's placeholder lines get replaced with real bullets
    once derived pages exist."""
    index_path = tmp_path / "index.md"
    index_path.write_text(_INDEX_WITH_DERIVED_SKELETON)

    _seed_derived_page(
        tmp_path,
        subdir="concepts",
        slug="anchor-step",
        frontmatter={
            "type": "concept",
            "slug": "anchor-step",
            "teachers": ["kate", "robert"],
            "sources": ["s1", "s2", "s3"],
            "status": "draft",
        },
    )
    _seed_derived_page(
        tmp_path,
        subdir="techniques",
        slug="whip",
        frontmatter={
            "type": "technique",
            "slug": "whip",
            "teachers": ["kaiano"],
            "sources": ["s1"],
            "status": "stub",
        },
    )
    _seed_derived_page(
        tmp_path,
        subdir="instructors",
        slug="kate",
        frontmatter={
            "type": "instructor",
            "slug": "kate",
            "sources_count": 18,
            "references_count": 3,
            "status": "draft",
        },
    )

    changed = regenerate_index_derived_sections(index_path)
    assert changed is True

    text = index_path.read_text()
    # Old placeholder text gone.
    assert "_No concept pages yet" not in text
    assert "_No technique pages yet" not in text
    assert "_No instructor pages yet" not in text
    # New bullets present.
    assert "[[concepts/anchor-step]]" in text
    assert "Taught by Kate, Robert (3 sources)" in text
    assert "[status: draft]" in text
    assert "[[techniques/whip]]" in text
    assert "Taught by Kaiano (1 source)" in text
    assert "[[instructors/kate]]" in text
    assert "18 sources · 3 referenced" in text
    # Terminology still shows placeholder since no terminology pages exist.
    assert "_No terminolog" in text
    # Untouched sections preserved.
    assert "## Views" in text
    assert "views/full-model.md" in text
    assert "## Sources" in text


def test_regenerate_index_handles_missing_derived_dirs(tmp_path: Path) -> None:
    """The function tolerates a wiki where some derived dirs don't
    exist on disk yet."""
    index_path = tmp_path / "index.md"
    index_path.write_text(_INDEX_WITH_DERIVED_SKELETON)
    # No concepts/, techniques/, instructors/, terminology/ directories.

    changed = regenerate_index_derived_sections(index_path)
    assert changed is False
    # File still got rewritten with the placeholder bodies, no crash.
    text = index_path.read_text()
    assert "_No concept" in text or "## Concepts" in text


def test_regenerate_index_replaces_stale_entries(tmp_path: Path) -> None:
    """Re-running after pages were deleted produces a clean index."""
    index_path = tmp_path / "index.md"
    index_path.write_text(_INDEX_WITH_DERIVED_SKELETON)

    # First pass: seed two concepts.
    _seed_derived_page(
        tmp_path,
        subdir="concepts",
        slug="anchor-step",
        frontmatter={"type": "concept", "slug": "anchor-step"},
    )
    stale_path = _seed_derived_page(
        tmp_path,
        subdir="concepts",
        slug="stale-page",
        frontmatter={"type": "concept", "slug": "stale-page"},
    )
    regenerate_index_derived_sections(index_path)
    text = index_path.read_text()
    assert "[[concepts/stale-page]]" in text

    # Second pass: stale page deleted on disk → must be gone from index.
    stale_path.unlink()
    regenerate_index_derived_sections(index_path)
    text = index_path.read_text()
    assert "[[concepts/stale-page]]" not in text
    assert "[[concepts/anchor-step]]" in text


def test_regenerate_index_preserves_sources_section(tmp_path: Path) -> None:
    """Source entries upserted incrementally must NOT be touched by the
    derived-page regeneration."""
    index_path = tmp_path / "index.md"
    skeleton_with_source = _INDEX_WITH_DERIVED_SKELETON.replace(
        "_No sources ingested yet._",
        "- [[sources/kate/2025-09-15-anchors]] — 2025-09-15 · Kate · private lesson",
    )
    index_path.write_text(skeleton_with_source)
    _seed_derived_page(
        tmp_path,
        subdir="concepts",
        slug="settle",
        frontmatter={"type": "concept", "slug": "settle"},
    )

    regenerate_index_derived_sections(index_path)
    text = index_path.read_text()
    # Source entry survived.
    assert "[[sources/kate/2025-09-15-anchors]]" in text
    # New concept entry showed up.
    assert "[[concepts/settle]]" in text
