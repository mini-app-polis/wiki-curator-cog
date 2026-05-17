"""Deterministic fan-out of source notes_json into derived wiki pages.

For each source, walk its ``notes_json`` and contribute to the four
derived page types per the field → page-type mapping in
``wcs-wiki/CLAUDE.md``:

  - ``concepts/<slug>.md``   ← key_concepts, conceptual vocabulary_terms
  - ``techniques/<slug>.md`` ← patterns_and_sequences
  - ``instructors/<slug>.md`` ← references (stubs) + sources where
                                 instructor leads
  - ``terminology/<slug>.md`` ← (reserved for cross-source synonym
                                 escalation; not yet emitted by this
                                 deterministic pass — see LATER)

No LLM. The upstream pipeline already extracted structured
concepts/terms/patterns; the curator's job is organization.

Idempotency contract: each contribution is keyed by source_slug. A
second ingest of the same source replaces that source's existing
contribution paragraph in place rather than appending a duplicate.

LATER (deferred LLM passes, not in this module):

- Identity / merge proposals across concept slugs that look like
  variants of the same idea (anchor-step / the-anchor / anchor).
- ``## Across sources`` synthesis paragraphs (convergence / divergence).
- ``terminology/`` page creation when several sources use related-but-
  not-identical vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import markdown_utils as md
from .slugs import slugify

PageType = Literal["concept", "technique", "instructor", "terminology"]


# ── Quality filters ─────────────────────────────────────────────────────
# The upstream notes-ingest-cog LLM extractor sometimes puts paragraph-
# shaped content into ``key_concept.concept`` (where a 5-12-word
# noun-phrase belongs) and non-person entities into ``references[i]``
# (events, schools, objects). Faithfully rendering all of it produces
# a derived layer dominated by one-off pages that drown the real
# cross-source concepts. These filters drop the worst offenders BEFORE
# a derived page gets created. The source page still renders the full
# notes_json verbatim — only the derived-page fanout is filtered.
#
# These filters are workarounds for an upstream data-quality issue.
# The real fix is constraining the extractor prompt to produce clean
# concept/reference fields. When that lands, re-extraction +
# curator_version bump will produce a clean derived layer with these
# filters becoming mostly no-op.

_CONCEPT_NAME_MAX_WORDS: int = 6  # "directional intent away from partner" = 5
_INSTRUCTOR_NAME_MAX_WORDS: int = 3  # "Robert Royston" or "PJ" or "John M"

# Reference types that pass the instructor-page filter without further
# name-shape checks. Lowercase comparison. Anything not on this list
# falls through to the name-shape heuristic.
_INSTRUCTOR_TYPE_ALLOWLIST: frozenset[str] = frozenset(
    {"instructor", "teacher", "coach", "judge", "dancer", "competitor", "pro"}
)

# Lowercase substrings whose presence in a reference name disqualifies
# it as an individual person. "and" / "&" catch multi-person entries
# ("Ben and Cameo", "KP and Bryn"); the rest catch the upstream LLM's
# hedge phrases.
_INSTRUCTOR_NAME_BLOCKLIST_SUBSTRINGS: tuple[str, ...] = (
    " and ",
    " & ",
    "not stated",
    "implied",
    "unknown",
    "various",
)


def _passes_concept_filter(concept_name: str) -> bool:
    """Heuristic: a real concept name is a noun-phrase, not a sentence.

    Drop if word-count exceeds the cap. Word-count is computed on the
    raw name (before slugify) so we don't trip on hyphens-as-separators
    in legitimate compound terms ("bow-and-snap" is one phrase, two
    hyphens).
    """
    words = concept_name.split()
    return 0 < len(words) <= _CONCEPT_NAME_MAX_WORDS


def _passes_instructor_filter(name: str, ref_type: str) -> bool:
    """Heuristic: a real instructor reference is an individual person.

    Allowed if:
      - ``ref_type`` matches the allowlist (caller's claim that this is a
        person we should track), OR
      - ``name`` shape passes: 1-3 whitespace-separated words, no
        blocklist substrings.

    The combined check tolerates the upstream LLM's inconsistent type
    population while still excluding events, schools, and multi-person
    entries when no type signal exists.
    """
    if not name or not name.strip():
        return False

    lowered = name.lower()
    for blocked in _INSTRUCTOR_NAME_BLOCKLIST_SUBSTRINGS:
        if blocked in lowered:
            return False

    if ref_type and ref_type.lower() in _INSTRUCTOR_TYPE_ALLOWLIST:
        return True

    # No usable type; fall back to name shape.
    words = name.split()
    if not 1 <= len(words) <= _INSTRUCTOR_NAME_MAX_WORDS:
        return False
    # Each word should look name-shaped: starts with an alphabetic char.
    # Accept things like "PJ" (initials), "Kate", "O'Brien". Reject
    # numeric leads ("2024-finals", "100"), things starting with weird
    # punctuation.
    for word in words:
        if not word or not word[0].isalpha():
            return False
    return True


# ── Data model ──────────────────────────────────────────────────────────


@dataclass
class Contribution:
    """One source's contribution to one derived page.

    ``teacher`` is the canonical instructor slug under which this
    contribution lands in ``## By teacher`` (None for non-attributed
    contributions like instructor-page "Referenced by" entries).

    ``paragraph_md`` is the markdown text to land under the teacher's
    subsection. Must include the source citation (e.g. trailing
    ``([[sources/<bucket>/<slug>]])``). The upsert checks for that
    citation token to detect "is this an existing contribution from
    the same source?" for idempotency.

    ``kind`` is the contribution flavor — most are ``by-teacher`` but
    some are special (``referenced-by`` for instructor pages, etc.) and
    the page-specific upsert dispatches on it.
    """

    page_type: PageType
    page_slug: str
    teacher: str | None
    paragraph_md: str
    source_slug: str
    source_bucket: str
    kind: Literal["by-teacher", "referenced-by"] = "by-teacher"
    # Optional payloads for richer page sections:
    common_mistakes: list[tuple[str, str]] = field(default_factory=list)


# ── Planning (pure, no IO) ──────────────────────────────────────────────


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _source_citation(source_bucket: str, source_slug: str) -> str:
    return f"([[sources/{source_bucket}/{source_slug}]])"


def _format_teacher_paragraph(detail: str, source_bucket: str, source_slug: str) -> str:
    """Render one teacher's contribution paragraph for a derived page.

    Shape: ``<detail prose> <citation>``. Citation goes at the end so
    the prose reads naturally.
    """
    detail = detail.strip()
    citation = _source_citation(source_bucket, source_slug)
    if not detail:
        return citation
    # Ensure detail ends in proper sentence punctuation before citation.
    if detail[-1] not in ".!?":
        detail = detail + "."
    return f"{detail} {citation}"


def plan_contributions(
    *,
    notes_json: dict[str, Any],
    canonical_instructors: list[str],
    source_slug: str,
    source_bucket: str,
) -> list[Contribution]:
    """Walk notes_json deterministically and return all the contributions.

    Pure function — doesn't touch disk. Caller invokes
    ``apply_contributions`` to write them out.

    Attribution rule (per CLAUDE.md "Multi-instructor sources"): each
    contribution is attributed to ALL canonical_instructors on the
    source, unless the data itself attributes a specific claim to a
    specific person (e.g. ``quotes[].speaker``). Defaults to all.

    Skipped here (handled elsewhere or deferred):
    - ``student_observations`` — never enters the wiki, per spec.
    - ``action_items`` — never enters the wiki, per spec.
    - ``off_topic_notes`` / ``suggested_new_sections`` — operational
      signals only.
    - ``competition_notes`` — would route to a ``competition-strategy``
      concept page; deferred until we have enough to make it useful.
    """
    contributions: list[Contribution] = []

    # ── key_concepts → concept pages ────────────────────────────────
    for item in _as_list(notes_json.get("key_concepts")):
        if isinstance(item, dict):
            name = _as_str(item.get("concept"))
            detail = _as_str(item.get("detail"))
        else:
            name = _as_str(item)
            detail = ""
        if not name:
            continue
        if not _passes_concept_filter(name):
            # Sentence-shaped "concept" name from upstream — don't
            # create a derived page for it. The full prose still
            # appears on the source page's ## Key concepts section.
            continue
        slug = slugify(name)
        if not slug:
            continue
        para = _format_teacher_paragraph(
            detail or f"({name})",
            source_bucket,
            source_slug,
        )
        for teacher in canonical_instructors or [None]:  # type: ignore[list-item]
            contributions.append(
                Contribution(
                    page_type="concept",
                    page_slug=slug,
                    teacher=teacher,
                    paragraph_md=para,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                )
            )

    # ── patterns_and_sequences → technique pages ────────────────────
    for item in _as_list(notes_json.get("patterns_and_sequences")):
        if isinstance(item, dict):
            name = _as_str(item.get("name"))
            description = _as_str(item.get("description"))
        else:
            name = _as_str(item)
            description = ""
        if not name:
            continue
        slug = slugify(name)
        if not slug:
            continue
        para = _format_teacher_paragraph(
            description or f"({name})",
            source_bucket,
            source_slug,
        )
        for teacher in canonical_instructors or [None]:  # type: ignore[list-item]
            contributions.append(
                Contribution(
                    page_type="technique",
                    page_slug=slug,
                    teacher=teacher,
                    paragraph_md=para,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                )
            )

    # ── vocabulary_terms → concept pages (term as concept name) ─────
    # Per CLAUDE.md vocabulary rules: case/plural variants merge
    # silently (slugify handles that), ambiguous synonym groups escalate
    # to terminology pages. Escalation is deferred to the LLM pass; for
    # now each term lands as its own concept page.
    for item in _as_list(notes_json.get("vocabulary_terms")):
        if isinstance(item, dict):
            term = _as_str(item.get("term"))
            definition = _as_str(item.get("definition"))
        else:
            term = _as_str(item)
            definition = ""
        if not term:
            continue
        if not _passes_concept_filter(term):
            continue
        slug = slugify(term)
        if not slug:
            continue
        para = _format_teacher_paragraph(
            f"**{term}** — {definition}" if definition else f"**{term}**",
            source_bucket,
            source_slug,
        )
        for teacher in canonical_instructors or [None]:  # type: ignore[list-item]
            contributions.append(
                Contribution(
                    page_type="concept",
                    page_slug=slug,
                    teacher=teacher,
                    paragraph_md=para,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                )
            )

    # ── references → instructor pages (referenced-by stubs) ─────────
    for item in _as_list(notes_json.get("references")):
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        ref_type = _as_str(item.get("type"))
        context = _as_str(item.get("context"))
        if not name:
            continue
        if not _passes_instructor_filter(name, ref_type):
            # Reference is an event/school/object/multi-person/hedged
            # placeholder — don't create an instructor page for it.
            # The reference still appears on the source page's
            # ## References section verbatim.
            continue
        slug = slugify(name)
        if not slug:
            continue
        citation = _source_citation(source_bucket, source_slug)
        type_tag = f" _({ref_type})_" if ref_type else ""
        context_part = f" — {context}" if context else ""
        para = f"**{name}**{type_tag}{context_part} {citation}"
        contributions.append(
            Contribution(
                page_type="instructor",
                page_slug=slug,
                teacher=None,
                paragraph_md=para,
                source_slug=source_slug,
                source_bucket=source_bucket,
                kind="referenced-by",
            )
        )

    return contributions


# ── Application (writes to disk) ────────────────────────────────────────


def _new_concept_page(slug: str) -> md.Page:
    return md.Page(
        frontmatter={
            "type": "concept",
            "slug": slug,
            "aliases": [],
            "sources": [],
            "teachers": [],
            "related": [],
            "status": "stub",
        },
        preamble=[],
        sections=[
            ("Overview", []),
            ("By teacher", []),
        ],
    )


def _new_technique_page(slug: str) -> md.Page:
    return md.Page(
        frontmatter={
            "type": "technique",
            "slug": slug,
            "aliases": [],
            "sources": [],
            "teachers": [],
            "related": [],
            "status": "stub",
        },
        preamble=[],
        sections=[
            ("Overview", []),
            ("By teacher", []),
        ],
    )


def _new_instructor_page(slug: str) -> md.Page:
    name_guess = " ".join(part.capitalize() for part in slug.split("-"))
    return md.Page(
        frontmatter={
            "type": "instructor",
            "slug": slug,
            "name": name_guess,
            "sources_count": 0,
            "references_count": 0,
            "concepts_taught": [],
            "techniques_taught": [],
            "status": "stub",
        },
        preamble=[],
        sections=[
            ("Background", []),
            ("Teaching themes", []),
            ("Notable framings", []),
            ("Sources", []),
            ("Referenced by", []),
        ],
    )


_NEW_PAGE_BUILDERS: dict[PageType, Any] = {
    "concept": _new_concept_page,
    "technique": _new_technique_page,
    "instructor": _new_instructor_page,
}


def _load_or_new_page(path: Path, page_type: PageType, slug: str) -> md.Page:
    if path.exists():
        return md.parse(path.read_text())
    return _NEW_PAGE_BUILDERS[page_type](slug)


def _upsert_teacher_paragraph(
    page: md.Page,
    *,
    teacher: str,
    paragraph_md: str,
    source_bucket: str,
    source_slug: str,
) -> None:
    """Ensure ``page`` has ``## By teacher`` → ``### <Teacher>`` containing this paragraph.

    Idempotent by source_slug: if a paragraph in that subsection
    already cites the same source, it's replaced; otherwise the new
    paragraph is appended.
    """
    teacher_heading = " ".join(part.capitalize() for part in teacher.split("-"))
    citation = _source_citation(source_bucket, source_slug)

    by_teacher = page.ensure_section("By teacher")
    chunks = md.split_h3(by_teacher)

    # Find this teacher's subsection (or create).
    found_index: int | None = None
    for i, (heading, _body) in enumerate(chunks):
        if heading is not None and heading.casefold() == teacher_heading.casefold():
            found_index = i
            break
    if found_index is None:
        chunks.append((teacher_heading, []))
        found_index = len(chunks) - 1

    heading, body = chunks[found_index]

    # The subsection's body is a list of paragraphs separated by blank
    # lines. Split into paragraphs, replace any whose text contains
    # our source citation, else append.
    paragraphs = _split_paragraphs(body)
    replaced = False
    for j, para_lines in enumerate(paragraphs):
        if any(citation in line for line in para_lines):
            paragraphs[j] = paragraph_md.splitlines()
            replaced = True
            break
    if not replaced:
        paragraphs.append(paragraph_md.splitlines())

    chunks[found_index] = (heading, _join_paragraphs(paragraphs))
    new_by_teacher = md.join_h3(chunks)
    page.set_section("By teacher", new_by_teacher)


def _upsert_referenced_by(
    page: md.Page,
    *,
    paragraph_md: str,
    source_bucket: str,
    source_slug: str,
) -> None:
    """Append (or replace) a bullet under ``## Referenced by`` keyed by source."""
    section = page.ensure_section("Referenced by")
    citation = _source_citation(source_bucket, source_slug)

    bullet = f"- {paragraph_md}"

    # Preserve any prose preamble, replace the matching bullet, or
    # append a new one.
    new_lines: list[str] = []
    replaced = False
    seen_bullet_block = False
    for line in section:
        stripped = line.strip()
        is_our_bullet = stripped.startswith("- ") and citation in stripped
        if is_our_bullet and not replaced:
            new_lines.append(bullet)
            replaced = True
            seen_bullet_block = True
            continue
        if stripped.startswith("- "):
            seen_bullet_block = True
        new_lines.append(line)

    if not replaced:
        if seen_bullet_block:
            new_lines.append(bullet)
        else:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(bullet)

    page.set_section("Referenced by", new_lines)


def _split_paragraphs(lines: list[str]) -> list[list[str]]:
    """Split body lines into paragraphs separated by blank lines."""
    out: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                out.append(current)
                current = []
            continue
        current.append(line)
    if current:
        out.append(current)
    return out


def _join_paragraphs(paragraphs: list[list[str]]) -> list[str]:
    """Inverse of _split_paragraphs. One blank line between paragraphs."""
    out: list[str] = []
    for i, para in enumerate(paragraphs):
        if i > 0:
            out.append("")
        out.extend(para)
    return out


def apply_contributions(
    contributions: list[Contribution],
    *,
    wiki_repo_path: Path,
) -> list[Path]:
    """Apply each contribution to its target derived page. Return touched paths.

    Idempotent: re-applying the same contribution is a no-op (paragraph
    is replaced in place, frontmatter sets don't grow). Pages are
    created on first contribution. Paths returned are absolute and
    deduplicated, in stable order (page_type then slug).
    """
    touched: dict[Path, None] = {}  # dict-as-ordered-set
    # Pre-group by (page_type, page_slug) so multiple contributions
    # to the same page open the file once.
    grouped: dict[tuple[PageType, str], list[Contribution]] = {}
    for c in contributions:
        grouped.setdefault((c.page_type, c.page_slug), []).append(c)

    for (page_type, slug), bucket in sorted(grouped.items()):
        page_dir = wiki_repo_path / f"{page_type}s"
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / f"{slug}.md"
        page = _load_or_new_page(page_path, page_type, slug)

        for c in bucket:
            if c.kind == "referenced-by":
                _upsert_referenced_by(
                    page,
                    paragraph_md=c.paragraph_md,
                    source_bucket=c.source_bucket,
                    source_slug=c.source_slug,
                )
                md.add_unique(
                    page.frontmatter, "references_count_sources", c.source_slug
                )
                # Maintain references_count as a derived integer.
                page.frontmatter["references_count"] = len(
                    page.frontmatter.get("references_count_sources", [])
                )
            else:
                # by-teacher (default)
                if c.teacher is not None:
                    _upsert_teacher_paragraph(
                        page,
                        teacher=c.teacher,
                        paragraph_md=c.paragraph_md,
                        source_bucket=c.source_bucket,
                        source_slug=c.source_slug,
                    )
                    md.add_unique(page.frontmatter, "teachers", c.teacher)
                    if page_type == "concept":
                        # No concepts_taught list on concept pages; this
                        # is the instructor-page reverse index.
                        pass
                md.add_unique(page.frontmatter, "sources", c.source_slug)

        page_path.write_text(md.serialize(page))
        touched[page_path] = None

    return list(touched.keys())


def derived_slugs_by_type(contributions: list[Contribution]) -> dict[str, list[str]]:
    """Return ``{page_type+"s": [slug, ...]}`` for the source's contributed_to frontmatter.

    Keys match the frontmatter shape: ``concepts``, ``techniques``,
    ``instructors``, ``terminology``. Slugs are deduplicated and sorted
    for stable output.
    """
    out: dict[str, set[str]] = {
        "concepts": set(),
        "techniques": set(),
        "instructors": set(),
        "terminology": set(),
    }
    for c in contributions:
        key = f"{c.page_type}s"
        if key in out:
            out[key].add(c.page_slug)
    return {k: sorted(v) for k, v in out.items()}
