"""Tests for inventory building."""

from __future__ import annotations

import uuid
from pathlib import Path

from wiki_curator_cog.inventory import build_inventory


def _write_source_page(
    wiki_repo_path: Path,
    bucket: str,
    slug: str,
    *,
    note_id: uuid.UUID,
    curator_version: int = 1,
) -> Path:
    path = wiki_repo_path / "sources" / bucket / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
type: source
note_id: {note_id}
transcript_id: {uuid.uuid4()}
curator_version: {curator_version}
---

# {slug}
"""
    )
    return path


def test_empty_repo_has_empty_inventory(wiki_repo_path: Path) -> None:
    inv = build_inventory(wiki_repo_path)
    assert inv.source_records == {}
    assert inv.concept_slugs == set()
    assert inv.technique_slugs == set()


def test_source_pages_indexed_by_note_id(wiki_repo_path: Path) -> None:
    note_id = uuid.uuid4()
    _write_source_page(wiki_repo_path, "kate", "2025-09-15-test", note_id=note_id)

    inv = build_inventory(wiki_repo_path)
    assert inv.has_note(note_id)
    record = inv.existing_record(note_id)
    assert record is not None
    assert record.curator_version == 1


def test_concept_slugs_collected_by_filename(wiki_repo_path: Path) -> None:
    (wiki_repo_path / "concepts" / "anchor-step.md").write_text("# anchor")
    (wiki_repo_path / "concepts" / "settle.md").write_text("# settle")
    inv = build_inventory(wiki_repo_path)
    assert inv.concept_slugs == {"anchor-step", "settle"}


def test_underscore_and_dot_prefixed_files_excluded(wiki_repo_path: Path) -> None:
    (wiki_repo_path / "instructors" / "_aliases.md").write_text("ignore")
    (wiki_repo_path / "instructors" / ".hidden.md").write_text("ignore")
    (wiki_repo_path / "instructors" / "kate.md").write_text("# kate")
    inv = build_inventory(wiki_repo_path)
    assert inv.instructor_slugs == {"kate"}


def test_source_page_without_note_id_is_ignored(wiki_repo_path: Path) -> None:
    path = wiki_repo_path / "sources" / "kate" / "no-frontmatter.md"
    path.write_text("# no frontmatter at all")
    inv = build_inventory(wiki_repo_path)
    assert inv.source_records == {}
