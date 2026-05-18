"""Idempotent edits to the wiki's catalog files (index.md, log.md).

The wiki repo carries two cross-cutting files that the curator touches
on every ingest:

  - ``index.md`` — the catalog of all pages. The curator inserts (or
    replaces) one line per source under the ``## Sources`` section,
    and at end of backfill regenerates the ``## Concepts`` /
    ``## Techniques`` / ``## Instructors`` / ``## Terminology``
    sections from current disk state.
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

import yaml

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


# ── index.md derived-page section regeneration ─────────────────────────
#
# The ``## Concepts``, ``## Techniques``, ``## Instructors``, and
# ``## Terminology`` sections of index.md are owned by the curator and
# fully regenerated from disk state at end of backfill (and at end of
# incremental flows when anything in those directories changed).
#
# Unlike the source index — which is upserted incrementally per-ingest
# so the section is always coherent mid-backfill — the derived sections
# are rebuilt as one operation because the curator wipes the four
# derived directories before backfill and would otherwise accumulate
# entries for pages that no longer exist.


# Page types that get their own index section, in the order they
# appear in index.md.
_DERIVED_SECTION_ORDER: tuple[tuple[str, str], ...] = (
    # (heading, subdirectory)
    ("Concepts", "concepts"),
    ("Techniques", "techniques"),
    ("Instructors", "instructors"),
    ("Terminology", "terminology"),
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _read_page_frontmatter(path: Path) -> dict:
    """Parse just the YAML frontmatter block from a derived page.

    Returns an empty dict for files without a frontmatter block or with
    malformed YAML — the caller treats those as "no metadata" and
    falls back to the bare wikilink form.
    """
    try:
        text = path.read_text()
    except OSError:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return {}
    try:
        parsed = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _format_derived_index_line(*, subdir: str, slug: str, fm: dict) -> str:
    """One bullet for a derived page in the index.

    Shape: ``- [[<subdir>/<slug>]] — <summary>``
    where ``<summary>`` is derived from frontmatter where possible:

      - For concept/technique pages: ``Taught by X, Y (N sources)``
        when ``teachers`` and ``sources`` are present.
      - For instructor pages: ``N sources · N referenced``.
      - For terminology pages: ``Reconciles: <term1>, <term2>``.
      - Always tags the page's ``status:`` at the end if non-default.

    Pages with no useful frontmatter render as bare wikilinks.
    """
    summary_parts: list[str] = []
    if subdir in {"concepts", "techniques"}:
        teachers = fm.get("teachers") or []
        sources = fm.get("sources") or []
        if isinstance(teachers, list) and teachers:
            pretty_teachers = ", ".join(
                " ".join(part.capitalize() for part in t.split("-")) for t in teachers
            )
            count = len(sources) if isinstance(sources, list) else 0
            noun = "source" if count == 1 else "sources"
            summary_parts.append(f"Taught by {pretty_teachers} ({count} {noun})")
    elif subdir == "instructors":
        sources_count = fm.get("sources_count") or 0
        references_count = fm.get("references_count") or 0
        if sources_count or references_count:
            summary_parts.append(
                f"{sources_count} source{'' if sources_count == 1 else 's'} · "
                f"{references_count} referenced"
            )
    elif subdir == "terminology":
        terms = fm.get("terms") or []
        if isinstance(terms, list) and terms:
            summary_parts.append("Reconciles: " + ", ".join(terms))

    status = fm.get("status")
    if status and status != "stub":
        summary_parts.append(f"[status: {status}]")

    summary = " · ".join(summary_parts)
    line = f"- [[{subdir}/{slug}]]"
    if summary:
        line += f" — {summary}"
    return line


def _list_derived_pages(wiki_repo_path: Path, subdir: str) -> list[Path]:
    """Return derived-page markdown files in ``<wiki>/<subdir>/`` sorted by slug.

    Filters out ``_aliases.yaml`` and any other non-markdown files.
    Returns an empty list if the directory doesn't exist.
    """
    directory = wiki_repo_path / subdir
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".md")


def _render_derived_section(wiki_repo_path: Path, subdir: str) -> list[str]:
    """Build the body lines for one derived section of index.md.

    Returns the body BELOW the heading (i.e. starts with a blank line,
    then bullets, then a blank line) so callers can drop it into the
    chunk structure used by ``_join_sections``. Empty directories
    render a single placeholder line so the section heading still has
    content under it.
    """
    pages = _list_derived_pages(wiki_repo_path, subdir)
    if not pages:
        placeholder = f"_No {subdir.rstrip('s')} pages yet._"
        return ["", placeholder, ""]

    lines: list[str] = [""]
    for page_path in pages:
        slug = page_path.stem
        fm = _read_page_frontmatter(page_path)
        lines.append(_format_derived_index_line(subdir=subdir, slug=slug, fm=fm))
    lines.append("")
    return lines


def regenerate_index_derived_sections(index_path: Path) -> bool:
    """Rewrite the four derived-page sections of index.md from disk state.

    Operates on the wiki repo containing ``index_path``. Looks at the
    ``concepts/``, ``techniques/``, ``instructors/``, ``terminology/``
    sibling directories and rebuilds the matching ``## <Heading>``
    sections of index.md from current page frontmatter.

    Sections that don't exist in index.md yet are appended at the end
    in the canonical order (Concepts → Techniques → Instructors →
    Terminology). The ``## Sources`` section and any other sections
    (preamble, ``## Views``, etc.) are left untouched.

    Returns True if index.md was rewritten, False if no derived
    directory has any markdown content (rare; only on a completely
    empty wiki). Safe to call repeatedly — output is fully deterministic
    from disk state.
    """
    if not index_path.exists():
        return False
    wiki_repo_path = index_path.parent

    # Decide ahead of time which sections we need to render — short-circuit
    # only if every derived directory is empty.
    has_any_pages = any(
        _list_derived_pages(wiki_repo_path, subdir)
        for _, subdir in _DERIVED_SECTION_ORDER
    )

    text = index_path.read_text()
    chunks = _split_at_sections(text)

    # Build a heading → body map for the new derived sections.
    rendered: dict[str, list[str]] = {}
    for heading, subdir in _DERIVED_SECTION_ORDER:
        rendered[heading] = _render_derived_section(wiki_repo_path, subdir)

    # Replace any existing matches in place; track which we still need
    # to append.
    handled: set[str] = set()
    for i, (heading, _body) in enumerate(chunks):
        if heading in rendered:
            chunks[i] = (heading, rendered[heading])
            handled.add(heading)

    # Append missing sections in canonical order, before any unrelated
    # tail sections (Views, Sources). We insert before the first
    # not-yet-handled, not-in-rendered section — usually Views or
    # Sources. If we can't find one, append at the very end.
    missing = [h for h, _ in _DERIVED_SECTION_ORDER if h not in handled]
    if missing:
        # Find the insertion point: first chunk whose heading is not in
        # ``rendered`` AND not None (preamble). That's typically the
        # Views section per the skeleton.
        insert_at = len(chunks)
        for i, (heading, _body) in enumerate(chunks):
            if heading is None:
                continue
            if heading not in rendered:
                insert_at = i
                break

        # ``missing`` is already in canonical order.
        new_chunks: list[tuple[str | None, list[str]]] = []
        new_chunks.extend(chunks[:insert_at])
        for heading in missing:
            new_chunks.append((heading, rendered[heading]))
        new_chunks.extend(chunks[insert_at:])
        chunks = new_chunks

    index_path.write_text(_join_sections(chunks))
    return has_any_pages


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
