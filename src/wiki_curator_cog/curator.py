"""Per-source ingest logic — the heart of wiki-curator-cog.

ingest_one_source(note, mode, inventory, aliases, wiki_repo_path) is the
single entry point. The function:

  1. Decides whether to skip (idempotency check against inventory).
  2. Writes the source page.
  3. Updates affected concept/technique/instructor/terminology pages.
  4. Updates index.md and appends to log.md.
  5. Returns a result describing what was touched.

Steps 2–3 require LLM-driven judgment (see wcs-wiki/CLAUDE.md). Phase 1
implementation will fill those in; this module currently defines the
shape and the deterministic parts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from mini_app_polis import logger as log

from .aliases import AliasMap
from .inventory import WikiInventory
from .models import WcsNote

if TYPE_CHECKING:
    from .api_client import WikiCuratorApiClient

LOG = log.get_logger()


class IngestMode(str, Enum):
    INTERACTIVE = "interactive"
    AUTOMATED = "automated"
    BACKFILL = "backfill"


@dataclass
class IngestResult:
    """What an ingest call produced."""

    note_id: str
    skipped: bool = False
    skip_reason: str | None = None
    source_path: Path | None = None
    touched_paths: list[Path] = field(default_factory=list)
    findings_emitted: int = 0


def ingest_one_source(
    *,
    note: WcsNote,
    mode: IngestMode,
    inventory: WikiInventory,
    aliases: AliasMap,
    wiki_repo_path: Path,
    api: "WikiCuratorApiClient",
    curator_version: int,
) -> IngestResult:
    """Ingest a single upstream note into the wiki.

    Idempotency contract: a note already present at the same
    curator_version is a no-op. Different curator_version triggers
    reprocessing.

    Phase 1 implementation will:
      - Resolve instructor/student slugs via aliases (escalating new
        variants to Kaiano in interactive mode, auto-adding in others).
      - Write the source page from notes_json per the schema in
        wcs-wiki/CLAUDE.md "Source pages" section.
      - Call the LLM to identify which existing wiki pages this source
        affects, and how, then apply updates.
      - Emit pipeline-evaluation findings for skipped judgment calls
        (automated mode).

    See wcs-wiki/CLAUDE.md "Ingest workflow" for the full sequence.
    """
    result = IngestResult(note_id=str(note.id))

    existing = inventory.existing_record(note.id)
    if existing is not None and existing.curator_version >= curator_version:
        result.skipped = True
        result.skip_reason = (
            f"already ingested at curator_version={existing.curator_version}"
        )
        LOG.info(
            "ingest.skip",
            extra={
                "note_id": str(note.id),
                "reason": result.skip_reason,
            },
        )
        return result

    # TODO Phase 1: implement the steps from CLAUDE.md "Ingest workflow".
    # The shape below is what callers will receive once it's filled in.
    LOG.warning(
        "ingest.not_implemented",
        extra={
            "note_id": str(note.id),
            "mode": mode.value,
            "session_type": note.session_type,
            "instructors": note.instructors,
            "students": note.students,
        },
    )
    raise NotImplementedError(
        "ingest_one_source is stubbed in this initial skeleton. "
        "Phase 1 implementation: see wcs-wiki/CLAUDE.md 'Ingest workflow' "
        "for the sequence."
    )
