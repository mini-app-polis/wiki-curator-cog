"""Idempotent edits to the wiki's catalog files (index.md, log.md).

The wiki repo carries two cross-cutting files that the curator touches
on every ingest:

  - ``index.md`` — the catalog of all pages. The curator inserts (or
    replaces) one line per source under the ``## Sources`` section.
  - ``log.md`` — an append-only chronological record. The curator
    appends one ``## [YYYY-MM-DD] ingest | <slug>`` entry per source.

Both helpers are designed to be safe to call repeatedly for the same
source. The source-page idempotency contract upstream (skip if
``curator_version`` matches) means the common case is "don't re-run at
all," but re-ingest at a bumped curator version is expected behavior
and shouldn't produce duplicate index entries.

Log entries are append-only by design — a re-ingest at a new curator
version is a real event worth recording, so we never dedupe log
entries.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

# ── index.md helpers ────────────────────────────────────────────────────

# Matches an H2 heading line (start-of-line ##, space, then any text).
_H2_RE = re.compile(r"^## (.+?)\s*$")

# Marker text used in the skeleton index.md. Replaced on first ingest
# under each section.
_PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "_No sources ingested yet._",
    "_No concept pages yet._",
    "_No technique pages yet._",
    "_No instructor pages yet._",
    "_No terminology pages yet._",
    "_No view pages yet._",
)

# Section heading the curator updates on every ingest. Sub-sections (like
# Views' bullet list of required v0 views) don't get touched here.
_SOURCES_HEADING: str = "Sources"

# A line we own in ``## Sources`` looks like:
#     - [[sources/<bucket>/<slug>]] — 2025-09-15 · Kate · private lesson
# The wikilink target uniquely identifies the source. We dedupe by that.
_SOURCE_WIKILINK_RE = re.compile(
    r"\[\[sources/(?P<bucket>[^/\]]+)/(?P<slug>[^\]\|]+)(?:\|[^\]]*)?\]\]"
)

# Date prefix at the start of a source slug (YYYY-MM-DD-...). Used to
# sort the Sources section newest-first. Slugs without a date prefix
# sort last (lexicographically) — they shouldn't exist in practice.
_SLUG_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _pretty_session_type(session_type: str) -> str:
    """``private_lesson`` → ``private lesson``. Display-only."""
    return session_type.replace("_", " ").strip()


def _pretty_instructors(canonical_instructors: list[str]) -> str:
    """Join canonical slugs with titlecase for the index summary line.

    Canonical slugs are lowercase (``kate``, ``kaiano-levine``); for
    display we title-case each whitespace-separated word. This is purely
    cosmetic — the source page itself stores both the canonical slug
    and the verbatim upstream name.
    """
    if not canonical_instructors:
        return "(unknown)"
    pretty = []
    for slug in canonical_instructors:
        pretty.append(" ".join(part.capitalize() for part in slug.split("-")))
    return ", ".join(pretty)


def format_source_index_line(
    *,
    source_slug: str,
    bucket: str,
    canonical_instructors: list[str],
    session_type: str,
    session_date: dt.date | None,
    title: str | None,
) -> str:
    """Build the markdown line that represents a source in ``## Sources``.

    Shape: ``- [[sources/<bucket>/<slug>]] — <date> · <instructors> · <session-type>[ · "<title>"]``
    Trailing newline is NOT included; the upsert helper adds line breaks.
    """
    date_s = session_date.isoformat() if session_date else "—"
    parts = [
        date_s,
        _pretty_instructors(canonical_instructors),
        _pretty_session_type(session_type),
    ]
    if title and title.strip():
        parts.append(f'"{title.strip()}"')
    summary = " · ".join(parts)
    return f"- [[sources/{bucket}/{source_slug}]] — {summary}"


def _slug_date_prefix(slug: str) -> str:
    m = _SLUG_DATE_PREFIX_RE.match(slug)
    return m.group(1) if m else ""


def _sort_source_lines_desc(lines: list[str]) -> list[str]:
    """Stable-sort source lines newest-first by slug date prefix.

    Lines that don't match the expected ``[[sources/.../<slug>]]``
    shape are left in place (treated as having an empty date prefix,
    which sorts to the bottom). Secondary sort: full line text, so
    same-day entries remain deterministic.
    """

    def key(line: str) -> tuple[str, str]:
        m = _SOURCE_WIKILINK_RE.search(line)
        if not m:
            return ("", line)
        # Negate the date by flipping characters? Simpler: return ("",
        # line) sentinel and reverse-sort by date below.
        return (_slug_date_prefix(m.group("slug")), line)

    # Sort ascending by date, then reverse so newest is first.
    return sorted(lines, key=key, reverse=True)


def _split_at_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split a markdown file into (section_title, body_lines) chunks.

    The first chunk has ``section_title=None`` and contains anything
    before the first ``## `` heading (title, intro prose, the ``---``
    rule under it). Subsequent chunks each start with their heading.
    """
    chunks: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_body: list[str] = []
    for line in text.splitlines():
        m = _H2_RE.match(line)
        if m is None:
            current_body.append(line)
            continue
        # Flush the previous chunk.
        chunks.append((current_title, current_body))
        current_title = m.group(1).strip()
        current_body = []
    chunks.append((current_title, current_body))
    return chunks


def _join_sections(chunks: list[tuple[str | None, list[str]]]) -> str:
    """Inverse of ``_split_at_sections``. Ensures a final newline."""
    parts: list[str] = []
    for title, body in chunks:
        if title is not None:
            parts.append(f"## {title}")
        parts.extend(body)
    text = "\n".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


def upsert_source_in_index(
    index_path: Path,
    *,
    source_slug: str,
    bucket: str,
    canonical_instructors: list[str],
    session_type: str,
    session_date: dt.date | None,
    title: str | None,
) -> None:
    """Insert or replace this source's line under ``## Sources``.

    Idempotent: if the section already contains a line whose wikilink
    target matches ``sources/<bucket>/<source_slug>``, that line is
    replaced (the new metadata may differ — e.g., the title was added
    by a re-extraction). Otherwise the line is inserted and the
    section is re-sorted newest-first.

    The placeholder ``_No sources ingested yet._`` is removed the
    first time we add a real entry under the section.
    """
    if not index_path.exists():
        raise FileNotFoundError(
            f"index.md not found at {index_path}. The wiki repo should "
            "carry an index.md skeleton — see wcs-wiki/README.md."
        )
    text = index_path.read_text()
    chunks = _split_at_sections(text)

    new_line = format_source_index_line(
        source_slug=source_slug,
        bucket=bucket,
        canonical_instructors=canonical_instructors,
        session_type=session_type,
        session_date=session_date,
        title=title,
    )
    target_wikilink_marker = f"[[sources/{bucket}/{source_slug}]]"

    found_section = False
    for i, (title_, body_lines) in enumerate(chunks):
        if title_ != _SOURCES_HEADING:
            continue
        found_section = True

        # Strip placeholder lines so the section becomes a real list.
        cleaned: list[str] = []
        for line in body_lines:
            stripped = line.strip()
            if any(stripped.startswith(p) for p in _PLACEHOLDER_PREFIXES):
                continue
            cleaned.append(line)

        # Separate the leading/trailing whitespace from the existing
        # entry lines. We rebuild as: one blank line above, sorted
        # entries, one blank line below.
        entries = [
            line for line in cleaned if line.strip() and line.lstrip().startswith("- ")
        ]
        # Drop any existing entry for this source.
        entries = [line for line in entries if target_wikilink_marker not in line]
        entries.append(new_line)
        entries = _sort_source_lines_desc(entries)

        chunks[i] = (title_, ["", *entries, ""])
        break

    if not found_section:
        # No ## Sources section in the file. Append one.
        chunks.append((_SOURCES_HEADING, ["", new_line, ""]))

    index_path.write_text(_join_sections(chunks))


def remove_source_from_index(
    index_path: Path,
    *,
    source_slug: str,
    bucket: str,
) -> bool:
    """Drop the entry for one source from the index's ``## Sources`` section.

    Used by the curator when a source moves buckets or changes slug
    between runs (e.g., after an alias-map edit collapses
    ``kate-benson`` → ``kate``, the v2 re-ingest writes to
    ``sources/kate/…`` and the old ``sources/external/…`` index line
    needs to disappear).

    No-op if the file doesn't exist, the section isn't found, or no
    matching line is present. Returns True if a line was actually
    removed, False otherwise — callers can use this to skip writing
    the file when nothing changed.
    """
    if not index_path.exists():
        return False

    text = index_path.read_text()
    chunks = _split_at_sections(text)
    target_wikilink_marker = f"[[sources/{bucket}/{source_slug}]]"
    removed = False

    for i, (title_, body_lines) in enumerate(chunks):
        if title_ != _SOURCES_HEADING:
            continue
        new_body: list[str] = []
        for line in body_lines:
            if target_wikilink_marker in line:
                removed = True
                continue
            new_body.append(line)
        if removed:
            chunks[i] = (title_, new_body)
        break

    if removed:
        index_path.write_text(_join_sections(chunks))
    return removed


# ── log.md helpers ──────────────────────────────────────────────────────


def append_log_entry(
    log_path: Path,
    *,
    action: str,
    subject: str,
    body: str,
    date: dt.date | None = None,
) -> None:
    """Append a new ``## [YYYY-MM-DD] <action> | <subject>`` block.

    Body is included verbatim (caller is responsible for formatting).
    Always appends — no dedupe, no idempotency check. The curator's
    upstream skip-if-already-ingested gate handles preventing double
    appends in the normal case; a re-ingest at a bumped curator
    version SHOULD produce a second log entry, by design.
    """
    if not log_path.exists():
        # Brand-new wiki — create the log with a minimal header so
        # subsequent appends produce a coherent file.
        log_path.write_text("# Log\n\n")

    date_str = (date or dt.date.today()).isoformat()
    entry = f"\n## [{date_str}] {action} | {subject}\n\n{body.strip()}\n"

    existing = log_path.read_text()
    # Guarantee a blank line between the previous entry and the new one.
    if not existing.endswith("\n"):
        existing += "\n"
    log_path.write_text(existing + entry)
