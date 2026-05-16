"""Wiki state inventory.

At the start of every run, the curator scans the wiki repo to build an
inventory of existing pages. The inventory tells the curator which
source pages already exist (idempotency: skip re-ingest at same
curator_version), which concept/technique/instructor/terminology slugs
exist (create vs. update), and the current canonical alias map.

See wcs-wiki/CLAUDE.md "Wiki state inventory" section for the contract.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SourceRecord:
    """One known source page in the inventory."""

    note_id: uuid.UUID
    path: Path
    curator_version: int


@dataclass
class WikiInventory:
    """Snapshot of wiki state at run start."""

    source_records: dict[uuid.UUID, SourceRecord] = field(default_factory=dict)
    concept_slugs: set[str] = field(default_factory=set)
    technique_slugs: set[str] = field(default_factory=set)
    instructor_slugs: set[str] = field(default_factory=set)
    terminology_slugs: set[str] = field(default_factory=set)
    view_slugs: set[str] = field(default_factory=set)

    def has_note(self, note_id: uuid.UUID) -> bool:
        return note_id in self.source_records

    def existing_record(self, note_id: uuid.UUID) -> SourceRecord | None:
        return self.source_records.get(note_id)


def _parse_frontmatter(text: str) -> dict | None:
    """Extract YAML frontmatter from a markdown file, or None if absent."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _iter_markdown_files(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.rglob("*.md") if p.is_file()]


def _collect_slugs(directory: Path) -> set[str]:
    """Collect slugs from markdown filenames in a directory (recursive).

    Slug = filename without .md extension. Files starting with _ or .
    are excluded (config and hidden files).
    """
    slugs: set[str] = set()
    for path in _iter_markdown_files(directory):
        stem = path.stem
        if stem.startswith(("_", ".")):
            continue
        slugs.add(stem)
    return slugs


def build_inventory(wiki_repo_path: Path) -> WikiInventory:
    """Walk the wiki repo and build a complete inventory snapshot."""
    inv = WikiInventory()

    # Source pages — read frontmatter to extract note_id and curator_version.
    sources_dir = wiki_repo_path / "sources"
    for path in _iter_markdown_files(sources_dir):
        fm = _parse_frontmatter(path.read_text())
        if fm is None:
            continue
        note_id_raw = fm.get("note_id")
        if not note_id_raw:
            continue
        try:
            note_id = uuid.UUID(str(note_id_raw))
        except (ValueError, TypeError):
            continue
        try:
            curator_version = int(fm.get("curator_version") or 0)
        except (ValueError, TypeError):
            curator_version = 0
        inv.source_records[note_id] = SourceRecord(
            note_id=note_id,
            path=path,
            curator_version=curator_version,
        )

    # Slug-bearing directories — filename-based collection.
    inv.concept_slugs = _collect_slugs(wiki_repo_path / "concepts")
    inv.technique_slugs = _collect_slugs(wiki_repo_path / "techniques")
    inv.instructor_slugs = _collect_slugs(wiki_repo_path / "instructors")
    inv.terminology_slugs = _collect_slugs(wiki_repo_path / "terminology")
    inv.view_slugs = _collect_slugs(wiki_repo_path / "views")

    return inv
