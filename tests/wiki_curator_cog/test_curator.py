"""End-to-end tests for ingest_one_source.

These tests stand up a tmp wiki-repo (via the wiki_repo_path fixture in
conftest), build a realistic WcsNote, and run the curator against it
without any git, network, or LLM activity. The goal is to validate the
Phase 1 deterministic ingest loop end-to-end.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from wiki_curator_cog.aliases import AliasMap
from wiki_curator_cog.curator import (
    IngestMode,
    ingest_one_source,
)
from wiki_curator_cog.inventory import build_inventory
from wiki_curator_cog.models import WcsNote
from wiki_curator_cog.slugs import UnknownNameError

# ── Helpers ─────────────────────────────────────────────────────────────


_INDEX_SKELETON = """# Index

Catalog of all pages in this wiki.

---

## Concepts

_No concept pages yet._

## Techniques

_No technique pages yet._

## Instructors

_No instructor pages yet._

## Terminology

_No terminology pages yet._

## Views

_No view pages yet._

## Sources

_No sources ingested yet._
"""

_LOG_SKELETON = (
    "# Log\n\n"
    "## [2026-05-16] schema-update | initial repo creation\n\n"
    "Initial commit.\n"
)


@pytest.fixture
def populated_wiki(wiki_repo_path: Path) -> Path:
    """Wiki repo with index.md and log.md skeletons in place."""
    (wiki_repo_path / "index.md").write_text(_INDEX_SKELETON)
    (wiki_repo_path / "log.md").write_text(_LOG_SKELETON)
    return wiki_repo_path


def _make_note(
    *,
    note_id: uuid.UUID | None = None,
    title: str | None = "Anchor step quality",
    session_date: dt.date | None = dt.date(2025, 9, 15),
    session_type: str = "private_lesson",
    instructors: list[str] | None = None,
    students: list[str] | None = None,
    notes_json: dict[str, Any] | None = None,
) -> WcsNote:
    return WcsNote.model_validate(
        dict(
            id=note_id or uuid.uuid4(),
            transcript_id=uuid.uuid4(),
            title=title,
            session_date=session_date,
            session_type=session_type,
            instructors=instructors or ["Kate"],
            students=students or ["Kaiano"],
            organization="Studio West",
            is_default_visible=True,
            visibility="private",
            model="claude-sonnet-4-6",
            provider="anthropic",
            notes_json=(
                notes_json
                if notes_json is not None
                else {
                    "summary": "Kate worked anchor step quality.",
                    "key_concepts": [
                        {
                            "concept": "Anchor step",
                            "detail": "Closing pattern.",
                        },
                    ],
                }
            ),
            created_at=dt.datetime(2025, 9, 15, 18, 0, tzinfo=dt.UTC),
        )
    )


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    assert text.startswith("---\n"), "Expected source page to start with YAML fence"
    body = text[4:]
    end = body.index("\n---\n")
    return yaml.safe_load(body[:end])


# ── Tests ───────────────────────────────────────────────────────────────


def test_ingest_writes_source_page_with_expected_frontmatter(
    populated_wiki: Path,
) -> None:
    note = _make_note()
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    result = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )

    assert result.skipped is False
    assert result.source_path is not None
    assert result.source_path.exists()
    # Bucketed under kate/, slug derived from date + title.
    assert result.source_path == populated_wiki / "sources" / "kate" / (
        "2025-09-15-anchor-step-quality.md"
    )

    fm = _frontmatter(result.source_path)
    assert fm["type"] == "source"
    assert fm["note_id"] == str(note.id)
    assert fm["instructors"] == ["kate"]
    assert fm["instructors_raw"] == ["Kate"]
    assert fm["students"] == ["kaiano"]
    assert fm["students_raw"] == ["Kaiano"]
    assert fm["curator_version"] == 1
    assert fm["session_date"] == "2025-09-15"
    assert fm["title"] == "Anchor step quality"


def test_ingest_updates_index_and_log(populated_wiki: Path) -> None:
    note = _make_note()
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    result = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )

    index_text = (populated_wiki / "index.md").read_text()
    assert "_No sources ingested yet._" not in index_text
    assert "[[sources/kate/2025-09-15-anchor-step-quality]]" in index_text

    log_text = (populated_wiki / "log.md").read_text()
    assert "ingest | 2025-09-15-anchor-step-quality" in log_text
    # Original schema-update entry preserved.
    assert "schema-update | initial repo creation" in log_text

    # touched_paths covers the source page, index, log, and any
    # derived (concept/technique/instructor) pages produced by the
    # deterministic fan-out. The default _make_note has one key_concept
    # ("Anchor step") so we expect at least one derived concept page.
    paths = {p.name for p in result.touched_paths}
    assert {"2025-09-15-anchor-step-quality.md", "index.md", "log.md"}.issubset(paths)
    assert "anchor-step.md" in paths


def test_ingest_auto_adds_new_aliases_in_backfill(populated_wiki: Path) -> None:
    note = _make_note(instructors=["Kate B"], students=["Sarah Johnson"])
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )

    assert aliases.to_slug("Kate B") == "kate-b"
    assert aliases.to_slug("Sarah Johnson") == "sarah-johnson"


def test_ingest_interactive_raises_on_unknown_name(populated_wiki: Path) -> None:
    note = _make_note(instructors=["Brand New Person"])
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    with pytest.raises(UnknownNameError):
        ingest_one_source(
            note=note,
            mode=IngestMode.INTERACTIVE,
            inventory=inventory,
            aliases=aliases,
            wiki_repo_path=populated_wiki,
            curator_version=1,
        )


def test_ingest_idempotent_at_same_curator_version(populated_wiki: Path) -> None:
    note = _make_note()
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    first = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    assert first.skipped is False

    second = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    assert second.skipped is True
    assert "already ingested" in (second.skip_reason or "")


def test_ingest_reprocesses_on_bumped_curator_version(populated_wiki: Path) -> None:
    note = _make_note()
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    first = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    # Re-build inventory and pass through with a higher curator_version
    # — this mirrors how flow.py would reload state between runs.
    inventory = build_inventory(populated_wiki)
    second = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=2,
    )
    assert second.skipped is False
    assert second.source_path == first.source_path
    fm = _frontmatter(second.source_path)  # type: ignore[arg-type]
    assert fm["curator_version"] == 2


def test_ingest_updates_in_memory_inventory(populated_wiki: Path) -> None:
    note = _make_note()
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    assert not inventory.has_note(note.id)
    ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    assert inventory.has_note(note.id)
    rec = inventory.existing_record(note.id)
    assert rec is not None
    assert rec.curator_version == 1


def test_ingest_moves_file_when_bucket_changes(populated_wiki: Path) -> None:
    """Re-ingest after an alias collapse should move the file atomically."""
    # First ingest: instructor "BrandNewPro" lands in external/ as
    # canonical 'brandnewpro' (auto-added).
    note = _make_note(instructors=["BrandNewPro"], title=None)
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    first = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    assert first.source_path is not None
    assert "sources/external/" in str(first.source_path).replace("\\", "/")
    assert first.removed_paths == []
    # Confirm index has the external/ entry.
    index_text = (populated_wiki / "index.md").read_text()
    assert "[[sources/external/" in index_text

    # Simulate manual alias collapse: BrandNewPro is actually Kate.
    aliases.add("BrandNewPro", "kate")
    inventory = build_inventory(populated_wiki)

    # Second ingest at bumped curator_version: bucket should switch
    # to kate/, old external/ file should be deleted, old index line
    # should be gone.
    second = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=2,
    )
    assert second.source_path is not None
    assert "sources/kate/" in str(second.source_path).replace("\\", "/")
    assert second.source_path != first.source_path
    # Old file gone, new file present.
    assert not first.source_path.exists()
    assert second.source_path.exists()
    # Move recorded in removed_paths.
    assert second.removed_paths == [first.source_path]
    # Index has new entry, no old entry.
    index_text = (populated_wiki / "index.md").read_text()
    assert "[[sources/kate/" in index_text
    assert "[[sources/external/" not in index_text
    # Log records the move.
    log_text = (populated_wiki / "log.md").read_text()
    assert "Source path changed" in log_text


def test_ingest_external_bucket_when_no_bucketed_instructor(
    populated_wiki: Path,
) -> None:
    note = _make_note(instructors=["Some Other Pro"])
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    result = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    assert result.source_path is not None
    assert "sources/external/" in str(result.source_path).replace("\\", "/")


def test_ingest_slug_collision_disambiguates(populated_wiki: Path) -> None:
    # Two distinct notes that would derive the same slug (no title, same
    # date, same instructor + session_type).
    note_a = _make_note(title=None, note_id=uuid.uuid4())
    note_b = _make_note(title=None, note_id=uuid.uuid4())
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    a = ingest_one_source(
        note=note_a,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )
    b = ingest_one_source(
        note=note_b,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        curator_version=1,
    )

    assert a.source_path != b.source_path
    assert a.source_path is not None and b.source_path is not None
    assert "-2.md" in b.source_path.name
    # Collision note recorded on the second source page.
    body = b.source_path.read_text()
    assert "## Notes" in body
    assert "collided" in body.lower()


def test_ingest_emits_quality_finding_for_empty_notes_json(
    populated_wiki: Path,
) -> None:
    # Track findings by attaching a tiny fake api that records calls.
    calls: list[dict[str, Any]] = []

    class FakeApi:
        def post_run_evaluation(self, **kw: Any) -> None:
            calls.append(kw)

    note = _make_note(notes_json={})
    inventory = build_inventory(populated_wiki)
    aliases = AliasMap.load(populated_wiki)

    result = ingest_one_source(
        note=note,
        mode=IngestMode.BACKFILL,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        api=FakeApi(),  # type: ignore[arg-type]
        curator_version=1,
    )
    assert result.findings_emitted >= 1
    assert any(c.get("dimension") == "wiki.source.quality_issue" for c in calls)
    # Quality observation also recorded on the page.
    assert "## Notes" in result.source_path.read_text()  # type: ignore[union-attr]


def test_ingest_interactive_mode_skips_findings_emission(
    populated_wiki: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeApi:
        def post_run_evaluation(self, **kw: Any) -> None:
            calls.append(kw)

    note = _make_note(notes_json={})
    # Seed aliases so interactive mode doesn't bail on unknown names.
    aliases = AliasMap.load(populated_wiki)
    aliases.add("Kate", "kate")
    aliases.add("Kaiano", "kaiano")
    inventory = build_inventory(populated_wiki)

    result = ingest_one_source(
        note=note,
        mode=IngestMode.INTERACTIVE,
        inventory=inventory,
        aliases=aliases,
        wiki_repo_path=populated_wiki,
        api=FakeApi(),  # type: ignore[arg-type]
        curator_version=1,
    )
    # Interactive mode: Kaiano is in the loop, no queue findings emitted.
    assert result.findings_emitted == 0
    assert calls == []
