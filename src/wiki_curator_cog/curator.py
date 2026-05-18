"""Per-source ingest logic — the heart of wiki-curator-cog.

``ingest_one_source(note, mode, inventory, aliases, wiki_repo_path)`` is the
single entry point. It:

  1. Decides whether to skip (idempotency check against inventory).
  2. Canonicalizes instructor/student names (mutating the alias map in
     backfill/automated mode; raising UnknownNameError in interactive
     mode so the caller can prompt Kaiano).
  3. Computes the source page's bucket directory and slug.
  4. Renders and writes the source page.
  5. Idempotently updates ``index.md`` and appends to ``log.md``.
  6. Updates the in-memory inventory so subsequent iterations in the
     same run see this note as already-ingested.
  7. Emits ``wiki.source.quality_issue`` and
     ``wiki.schema.suggested_section`` findings (automated/backfill modes).
  8. Returns an IngestResult describing what was touched.

Phase 1 scope: source pages only. Concept, technique, instructor, and
terminology pages are NOT created or updated here yet — that's the LLM
step, slated for Phase 1.5. The IngestResult's ``contributed_to`` lists
will all be empty until then.

See ``wcs-wiki/CLAUDE.md`` ("Ingest workflow") for the full operational
spec the curator is implementing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from mini_app_polis import logger as log

from .aliases import AliasMap
from .derived_pages import (
    apply_contributions,
    derived_slugs_by_type,
    plan_contributions,
)
from .inventory import SourceRecord, WikiInventory
from .models import WcsNote
from .rendering import detect_quality_issues, render_source_page
from .slugs import (
    bucket_for_instructors,
    build_source_slug,
    canonicalize_names,
)
from .wiki_files import (
    append_log_entry,
    remove_source_from_index,
    upsert_source_in_index,
)

if TYPE_CHECKING:
    from .api_client import WikiCuratorApiClient

LOG = log.get_logger()


class IngestMode(StrEnum):
    INTERACTIVE = "interactive"
    AUTOMATED = "automated"
    BACKFILL = "backfill"


# Modes that auto-add new names to the alias map vs. raising for
# operator intervention.
_AUTO_ADD_MODES: frozenset[IngestMode] = frozenset(
    {IngestMode.AUTOMATED, IngestMode.BACKFILL}
)


@dataclass
class IngestResult:
    """What an ingest call produced.

    ``touched_paths`` are files the curator wrote or modified (the
    source page, ``index.md``, ``log.md``). ``removed_paths`` are
    files the curator deleted from the working tree because the same
    note moved to a different path between curator versions — these
    must be staged with ``git rm`` semantics, not ``git add``. The
    flow layer handles that distinction via WikiRepo.stage_removal.
    """

    note_id: str
    skipped: bool = False
    skip_reason: str | None = None
    source_path: Path | None = None
    touched_paths: list[Path] = field(default_factory=list)
    removed_paths: list[Path] = field(default_factory=list)
    findings_emitted: int = 0


def _resolve_unique_source_path(
    wiki_repo_path: Path,
    *,
    bucket: str,
    base_slug: str,
    note_id: str,
) -> tuple[Path, str, str | None]:
    """Pick a non-colliding path for the source page.

    The base slug is derived from session_date + title-or-fallback;
    distinct notes can collide (same date, same title, different
    teachers). When the destination already exists for a DIFFERENT
    note_id, we suffix the slug (``-2``, ``-3``, …) until we find a
    free name.

    Returns ``(final_path, final_slug, collision_note)`` where
    ``collision_note`` is a human-readable line to add to the source
    page's ``## Notes`` section if a suffix was needed, or ``None`` if
    the base slug was usable as-is.
    """
    bucket_dir = wiki_repo_path / "sources" / bucket
    candidate_slug = base_slug
    candidate_path = bucket_dir / f"{candidate_slug}.md"
    collision_note: str | None = None
    suffix = 1

    while candidate_path.exists():
        # Re-ingest of the same note (already-processed at a lower
        # curator_version, falling through the inventory check because
        # the inventory was loaded BEFORE the bump) reuses the same
        # path. Distinguish by reading the existing frontmatter's
        # note_id.
        existing = candidate_path.read_text()
        if f"note_id: {note_id}" in existing:
            # Same note — overwrite is fine. Done.
            break
        suffix += 1
        candidate_slug = f"{base_slug}-{suffix}"
        candidate_path = bucket_dir / f"{candidate_slug}.md"
        if collision_note is None:
            collision_note = (
                f"Slug ``{base_slug}`` collided with an existing source "
                f"page for a different note. Disambiguated as "
                f"``{candidate_slug}``."
            )

    return candidate_path, candidate_slug, collision_note


def _emit_findings(
    api: WikiCuratorApiClient | None,
    *,
    mode: IngestMode,
    note_id: str,
    notes_json_observations: list[str],
) -> int:
    """Emit pipeline_evaluations findings for the relevant observations.

    Only emits in automated/backfill modes (interactive mode keeps
    Kaiano in the loop and doesn't need queue-based review). Returns
    the count of findings emitted. Silently no-ops when no API client
    was passed in (tests, dry runs).
    """
    if api is None or mode == IngestMode.INTERACTIVE:
        return 0
    if not notes_json_observations:
        return 0

    count = 0
    for obs in notes_json_observations:
        # Heuristic split: "Upstream LLM suggested" → schema dimension;
        # everything else → quality dimension. detect_quality_issues
        # produces both kinds.
        if obs.startswith("Upstream LLM suggested"):
            dimension = "wiki.schema.suggested_section"
        else:
            dimension = "wiki.source.quality_issue"
        try:
            api.post_run_evaluation(
                dimension=dimension,
                severity="WARN" if dimension.endswith(".quality_issue") else "INFO",
                finding=f"note_id={note_id}: {obs}",
            )
            count += 1
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            LOG.warning(
                "ingest.finding_emit_failed",
                extra={
                    "note_id": note_id,
                    "dimension": dimension,
                    "err": str(exc),
                },
            )
    return count


def ingest_one_source(
    *,
    note: WcsNote,
    mode: IngestMode,
    inventory: WikiInventory,
    aliases: AliasMap,
    wiki_repo_path: Path,
    api: WikiCuratorApiClient | None = None,
    curator_version: str,
    concept_aliases: AliasMap | None = None,
    technique_aliases: AliasMap | None = None,
) -> IngestResult:
    """Ingest a single upstream note into the wiki.

    See module docstring for the sequence. Mutations performed:

    - ``aliases`` may gain new entries (auto-add modes only). The
      caller is responsible for ``aliases.save()`` — typically once at
      end of run.
    - ``inventory`` gains a ``SourceRecord`` for this note. Subsequent
      ``inventory.has_note()`` calls will return True.
    - Files written under ``wiki_repo_path``: the source page,
      ``index.md``, and ``log.md``.
    """
    result = IngestResult(note_id=str(note.id))

    # ── 1. Idempotency check ────────────────────────────────────────
    # Equality (not >=) because curator_version is now a semver string
    # auto-derived from the package release. Any change to the cog —
    # release bump, manual env override, or rolling back to a previous
    # deploy — triggers a re-render. This is more correct than the
    # monotonic-int comparison: rolling back from v2 to v1 SHOULD
    # re-render everything against v1's behavior.
    existing = inventory.existing_record(note.id)
    if existing is not None and existing.curator_version == curator_version:
        result.skipped = True
        result.skip_reason = (
            f"already ingested at curator_version={existing.curator_version}"
        )
        LOG.info(
            "ingest.skip",
            extra={"note_id": str(note.id), "reason": result.skip_reason},
        )
        return result

    # ── 2. Canonicalize names ───────────────────────────────────────
    auto_add = mode in _AUTO_ADD_MODES
    canonical_instructors = canonicalize_names(
        note.instructors, aliases, auto_add=auto_add
    )
    canonical_students = canonicalize_names(note.students, aliases, auto_add=auto_add)

    # ── 3. Compute bucket + slug ────────────────────────────────────
    bucket = bucket_for_instructors(canonical_instructors)
    base_slug = build_source_slug(
        session_date=note.session_date,
        title=note.title,
        canonical_instructors=canonical_instructors,
        session_type=note.session_type,
        created_at=note.created_at,
    )
    source_path, final_slug, collision_note = _resolve_unique_source_path(
        wiki_repo_path,
        bucket=bucket,
        base_slug=base_slug,
        note_id=str(note.id),
    )

    # ── 3b. Detect move (bucket or slug changed since last ingest) ──
    # When the resolved path differs from the existing inventory
    # record's path, the source's bucket or slug has changed (alias
    # collapsed, title filled in, etc.). Plan to delete the old file
    # and its index entry so the per-source commit reflects an atomic
    # move rather than producing duplicates.
    moved_from: Path | None = None
    if (
        existing is not None
        and existing.path is not None
        and existing.path != source_path
        and existing.path.exists()
    ):
        moved_from = existing.path

    # ── 4. Build extra notes (quality + collision) ──────────────────
    quality_observations = detect_quality_issues(note.notes_json)
    extra_notes: list[str] = []
    extra_notes.extend(quality_observations)
    if collision_note:
        extra_notes.append(collision_note)

    # ── 4b. Plan derived-page contributions (pure) ──────────────────
    # Deterministic fan-out: walk notes_json and figure out which
    # concept/technique/instructor pages this source should land
    # contributions on. No disk writes yet; we need the list so the
    # source page's contributed_to frontmatter can reflect the
    # back-references before we render it.
    contributions = plan_contributions(
        notes_json=note.notes_json,
        canonical_instructors=canonical_instructors,
        source_slug=final_slug,
        source_bucket=bucket,
        concept_aliases=concept_aliases,
        technique_aliases=technique_aliases,
        # Source metadata flows through to the instructor-page bullet
        # under ``## Sources`` for each canonical instructor on this
        # source. See derived_pages._format_author_source_bullet.
        session_date=note.session_date,
        session_type=note.session_type,
        title=note.title,
    )
    contributed_to = derived_slugs_by_type(contributions)

    # ── 5. Render and write the source page ─────────────────────────
    rendered = render_source_page(
        note,
        canonical_instructors=canonical_instructors,
        canonical_students=canonical_students,
        instructors_raw=list(note.instructors),
        students_raw=list(note.students),
        contributed_to=contributed_to,
        curator_version=curator_version,
        ingested_at=dt.date.today(),
        extra_notes=extra_notes,
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(rendered)

    # ── 5a. Apply derived-page contributions ────────────────────────
    # Each contribution upserts a teacher paragraph (or referenced-by
    # entry) into the relevant derived page. Returns the absolute
    # paths of every page touched so the per-source commit captures
    # them all atomically with the source page.
    #
    # Partial-failure cleanup: if apply_contributions raises (e.g.
    # ENAMETOOLONG on a pathological slug, disk full, permission
    # error), the source page we just wrote in step 5 is left
    # uncommitted — but backfill_flow's residual-commit at end of run
    # picks up the orphan as an untracked file and commits it anyway,
    # poisoning the inventory slot with a curator_version=N frontmatter
    # that blocks any future re-ingest. Unlink the partial source page
    # before propagating the exception so the next run can retry.
    try:
        derived_paths = apply_contributions(
            contributions, wiki_repo_path=wiki_repo_path
        )
    except Exception:
        try:
            source_path.unlink()
        except OSError:
            pass  # already gone or unwritable; preserve the original error
        LOG.error(
            "ingest.apply_failed",
            extra={
                "note_id": str(note.id),
                "source_path": str(source_path.relative_to(wiki_repo_path)),
                "removed_partial": True,
            },
        )
        raise

    index_path = wiki_repo_path / "index.md"

    # ── 5b. Apply move (delete old file + remove its index line) ────
    # Done after writing the new file so the new content is in place
    # before we delete the old, in case anything goes wrong between
    # the two operations.
    if moved_from is not None:
        old_bucket = moved_from.parent.name
        old_slug = moved_from.stem
        remove_source_from_index(index_path, source_slug=old_slug, bucket=old_bucket)
        moved_from.unlink()
        result.removed_paths.append(moved_from)
        LOG.info(
            "ingest.moved",
            extra={
                "note_id": str(note.id),
                "from": str(moved_from.relative_to(wiki_repo_path)),
                "to": str(source_path.relative_to(wiki_repo_path)),
            },
        )

    # ── 6. Update index.md ──────────────────────────────────────────
    upsert_source_in_index(
        index_path,
        source_slug=final_slug,
        bucket=bucket,
        canonical_instructors=canonical_instructors,
        session_type=note.session_type,
        session_date=note.session_date,
        title=note.title,
    )

    # ── 7. Append to log.md ─────────────────────────────────────────
    log_path = wiki_repo_path / "log.md"
    log_body = _build_log_body(
        note=note,
        bucket=bucket,
        final_slug=final_slug,
        canonical_instructors=canonical_instructors,
        canonical_students=canonical_students,
        is_reingest=existing is not None,
        old_curator_version=existing.curator_version if existing else None,
        new_curator_version=curator_version,
        quality_observations=quality_observations,
        collision_note=collision_note,
        moved_from=moved_from,
        wiki_repo_path=wiki_repo_path,
        source_path=source_path,
    )
    append_log_entry(
        log_path,
        action="ingest",
        subject=final_slug,
        body=log_body,
    )

    # ── 8. Update inventory in-place ────────────────────────────────
    inventory.source_records[note.id] = SourceRecord(
        note_id=note.id,
        path=source_path,
        curator_version=curator_version,
    )

    # ── 9. Emit findings (automated/backfill only) ──────────────────
    result.findings_emitted = _emit_findings(
        api,
        mode=mode,
        note_id=str(note.id),
        notes_json_observations=quality_observations,
    )

    # ── 10. Fill in the result ──────────────────────────────────────
    result.source_path = source_path
    result.touched_paths = [
        source_path,
        index_path,
        log_path,
        *derived_paths,
    ]

    LOG.info(
        "ingest.complete",
        extra={
            "note_id": str(note.id),
            "mode": mode.value,
            "slug": final_slug,
            "bucket": bucket,
            "instructors": canonical_instructors,
            "students": canonical_students,
            "derived_pages": len(derived_paths),
            "findings_emitted": result.findings_emitted,
            "extra_notes": len(extra_notes),
        },
    )
    return result


def _build_log_body(
    *,
    note: WcsNote,
    bucket: str,
    final_slug: str,
    canonical_instructors: list[str],
    canonical_students: list[str],
    is_reingest: bool,
    old_curator_version: str | None,
    new_curator_version: str,
    quality_observations: list[str],
    collision_note: str | None,
    moved_from: Path | None = None,
    wiki_repo_path: Path | None = None,
    source_path: Path | None = None,
) -> str:
    """Compose the short body for a single log.md ingest entry."""
    lines: list[str] = []
    lines.append(
        f"Ingested source `{final_slug}` "
        f"(note_id `{note.id}`) into `sources/{bucket}/`."
    )
    if canonical_instructors:
        lines.append(
            f"Instructors: {', '.join(canonical_instructors)}. "
            f"Students: "
            + (", ".join(canonical_students) if canonical_students else "(none)")
            + "."
        )
    if is_reingest:
        lines.append(
            f"Re-ingest: was at curator_version={old_curator_version}, "
            f"now at {new_curator_version}."
        )
    if (
        moved_from is not None
        and wiki_repo_path is not None
        and source_path is not None
    ):
        lines.append(
            f"Source path changed: `{moved_from.relative_to(wiki_repo_path)}` "
            f"→ `{source_path.relative_to(wiki_repo_path)}` "
            f"(bucket or slug shifted after the alias map or title changed)."
        )
    if collision_note:
        lines.append(collision_note)
    if quality_observations:
        lines.append(
            f"Quality observations recorded in source page Notes section "
            f"({len(quality_observations)})."
        )
    return "\n\n".join(lines)
