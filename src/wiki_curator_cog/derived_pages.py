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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import markdown_utils as md
from .aliases import AliasMap
from .slugs import canonicalize_concept_slug, canonicalize_technique_slug, slugify

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

# ── Concept filter shape rules ──────────────────────────────────────────
# v4 (word-count only) let through too many sentence-shaped names like
# "eccentric vs concentric muscle engagement", "connect at or below the
# connection", "competitive ceiling competitive floor". These rules
# reject the recurring shapes we saw in the wiki audit. They're all
# lowercase, whitespace-tokenized checks.

# Conjunctions that signal a multi-concept smush rather than one
# noun-phrase. "X vs Y", "X or Y", "X versus Y" etc.
_CONCEPT_CONJUNCTION_TOKENS: frozenset[str] = frozenset(
    {"vs", "vs.", "versus", "or", "v"}
)

# Stop / function words that don't appear in noun-phrase concept names
# but DO appear in sentence-shaped names from the upstream LLM. Order:
# articles, prepositions, conjunctions of subordination. We check for
# these as interior tokens (not first/last) so legitimate names that
# start with "On the …" don't false-positive on day one.
_CONCEPT_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "without",
        "from",
        "into",
        "by",
        "as",
        "above",
        "below",
        "between",
        "before",
        "after",
        "when",
        "while",
        "until",
        "during",
        "via",
    }
)

# Suffix words that flag this as belonging on a technique page (a
# named variant) rather than as its own standalone concept page.
_CONCEPT_TECHNIQUE_SUFFIX_WORDS: frozenset[str] = frozenset(
    {"variation", "variations", "drill", "drills"}
)


def _passes_concept_filter(concept_name: str) -> bool:
    """Heuristic: a real concept name is a noun-phrase, not a sentence.

    Reject when any of the following hold:

      - Empty / blank.
      - More than ``_CONCEPT_NAME_MAX_WORDS`` whitespace-separated tokens.
      - Contains a conjunction token (``vs`` / ``or`` / ``versus``) that
        joins multiple concepts in one name.
      - Contains a stopword/function word in an interior position
        (``connect at or below the connection``, ``pulling the trigger
        on redirection``) — these are sentence-shaped, not noun-phrases.
      - Contains a repeated word (``competitive ceiling competitive
        floor``, ``walk walk triple step triple step``) — pretty
        reliably a smushed list rather than one concept.
      - Ends in a technique-variant suffix (``parallel hips sugar tuck
        variation``) — belongs on the parent technique page, not as its
        own concept.

    Word-count is computed on the raw name (before slugify) so we don't
    trip on hyphens-as-separators in legitimate compound terms
    (``bow-and-snap`` is one phrase, two hyphens).
    """
    if not concept_name or not concept_name.strip():
        return False
    # Treat hyphens as word separators for shape analysis — many upstream
    # values arrive pre-hyphenated ("eccentric-vs-concentric-muscle-
    # engagement") and we need to see "vs" as its own token.
    tokens = [t for t in re.split(r"[\s\-]+", concept_name.lower()) if t]
    if not 0 < len(tokens) <= _CONCEPT_NAME_MAX_WORDS:
        return False
    if any(tok in _CONCEPT_CONJUNCTION_TOKENS for tok in tokens):
        return False
    # Interior stopwords only — allow a name like "anchor" or "the
    # anchor" (first-position "the" is uncommon but not disqualifying;
    # last-position stopwords don't really occur in practice).
    if len(tokens) >= 3:
        interior = tokens[1:-1]
        if any(tok in _CONCEPT_STOPWORDS for tok in interior):
            return False
    if len(tokens) != len(set(tokens)):
        # A repeated word almost always means we're looking at a smushed
        # list ("X-X-Y-X-Y") rather than a concept.
        return False
    if tokens[-1] in _CONCEPT_TECHNIQUE_SUFFIX_WORDS:
        return False
    return True


# ── Instructor filter shape rules ───────────────────────────────────────
# v4 (allowlist of ref types + person-shape heuristic) let through
# events ("Cash Bash", "Champ Camps"), dance styles ("Argentine Tango",
# "Contact Improvisation"), org acronyms ("ASDC"), geographic event
# names ("Chicago Classic", "City of Angels"), and even a technique
# ("Chenet Turn"). These rules add explicit denials for the recurring
# non-person shapes we saw in the wiki audit.

# Dance style names. Match as whole tokens within the lowercased name.
_NON_PERSON_DANCE_STYLES: frozenset[str] = frozenset(
    {
        "tango",
        "salsa",
        "hustle",
        "bachata",
        "kizomba",
        "zouk",
        "lindy",
        "balboa",
        "blues",
        "ballroom",
        "jive",
        "samba",
        "rumba",
        "cha-cha",
        "improvisation",
        "wcs",
    }
)

# Event / competition / workshop name suffix tokens. If the name ends
# in any of these (case-insensitive), it's almost certainly an event,
# not a person.
_NON_PERSON_EVENT_SUFFIXES: frozenset[str] = frozenset(
    {
        "bash",
        "classic",
        "cup",
        "jam",
        "camp",
        "camps",
        "open",
        "finals",
        "champs",
        "championships",
        "championship",
        "convention",
        "competition",
        "comp",
        "festival",
        "weekend",
        "intensive",
        "workshop",
        "workshops",
        "battle",
        "showcase",
        "showdown",
    }
)

# Common geographic / generic words that appear in event names but never
# in person names. Matched as any-position whole tokens.
_NON_PERSON_PLACE_TOKENS: frozenset[str] = frozenset(
    {
        "city",
        "of",
        "angels",
        "academy",
        "studio",
        "school",
        "dance",
        "swing",
    }
)


def _looks_like_acronym(name: str) -> bool:
    """All-uppercase 3-5 char token with no whitespace — likely an org acronym.

    Two-letter all-caps strings (``PJ``, ``AJ``, ``TJ``, ``BJ``) are
    overwhelmingly personal initials/nicknames in the WCS corpus rather
    than organization acronyms, so they fall through to the name-shape
    check instead of being rejected here.
    """
    s = name.strip()
    return 3 <= len(s) <= 5 and s.isupper() and s.isalpha()


# Vowels for the loose-acronym shape detector below. ``y`` counts as a
# vowel so 4-letter names like ``Ardy`` aren't false-positive rejected.
_VOWELS: frozenset[str] = frozenset("aeiouy")


def _looks_like_loose_acronym(name: str) -> bool:
    """Catch acronym-shaped names that the case-sensitive check misses.

    The upstream LLM occasionally case-folds organizational acronyms
    (``ASDC`` → ``Asdc``, ``WSDC`` → ``Wsdc``) so the strict
    ``isupper()`` check in ``_looks_like_acronym`` doesn't fire. This
    looser variant rejects 3-5 letter alphabetic tokens that have an
    acronym-typical vowel profile:

      * Zero vowels at all (``Dcsx``, ``Wsdc``) — guaranteed acronym.
      * One vowel only at position 0 with no repeated letters
        (``Asdc``: a + s,d,c). Real human names with a single leading
        vowel tend to have a repeated consonant (``Anna``, ``Ella``,
        ``Adam``+initial-only) or are too short to gate on here.

    Real WCS-corpus 4-letter names that must still pass:

      * ``Kate`` — 2 vowels (a, e).
      * ``Ardy`` — 2 vowels (a, y).
      * ``Jess`` — 1 vowel mid-word + repeated consonant.
      * ``Hugh`` — 1 vowel mid-word + repeated consonant.
      * ``Joel`` — 2 vowels (o, e).
    """
    s = name.strip()
    if not (3 <= len(s) <= 5 and s.isalpha()):
        return False
    lowered = s.lower()
    vowel_positions = [i for i, c in enumerate(lowered) if c in _VOWELS]

    if not vowel_positions:
        return True

    if len(vowel_positions) == 1 and vowel_positions[0] == 0:
        # One vowel at the very start, no repeated characters →
        # acronym shape. Repeated characters suggest a real name like
        # ``Anna``/``Ella``.
        if len(set(lowered)) == len(lowered):
            return True

    return False


def _passes_instructor_filter(name: str, ref_type: str) -> bool:
    """Heuristic: a real instructor reference is an individual person.

    Two-stage check:

      1. Hard rejections that apply regardless of ``ref_type``: hedged
         placeholders ("not stated"), multi-person joiners ("and"/"&"),
         dance-style tokens, event-suffix tokens, place tokens, org
         acronyms. These shapes are reliably not-a-person even when
         upstream mis-tags them with ``type=instructor``.
      2. If ``ref_type`` is in the allowlist, allow. Otherwise fall back
         to a name-shape check: 1-3 alphabetic-led tokens.

    Returns True for real-looking individual people, False otherwise.
    """
    if not name or not name.strip():
        return False

    lowered = name.lower()

    # Stage 1: hard rejections.
    for blocked in _INSTRUCTOR_NAME_BLOCKLIST_SUBSTRINGS:
        if blocked in lowered:
            return False

    tokens = [t for t in re.split(r"[\s\-]+", lowered) if t]
    if not tokens:
        return False

    # Acronyms — usually orgs, not people. Strict (all-caps) catches
    # ``WSDC`` / ``NDC``; loose catches ``Asdc``/``Wsdc`` where the LLM
    # case-folded the acronym back to title-case.
    if _looks_like_acronym(name.strip()):
        return False
    if _looks_like_loose_acronym(name.strip()):
        return False

    # Dance style names (whole-token match).
    if any(tok in _NON_PERSON_DANCE_STYLES for tok in tokens):
        return False

    # Event-suffix names (suffix match on last token).
    if tokens[-1] in _NON_PERSON_EVENT_SUFFIXES:
        return False

    # Place / generic tokens (whole-token match).
    if any(tok in _NON_PERSON_PLACE_TOKENS for tok in tokens):
        return False

    # Stage 2: type-based allow, else shape fallback.
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

    ``kind`` is the contribution flavor:

      * ``by-teacher`` (default) — content lands under
        ``## By teacher`` → ``### <Teacher>`` on a concept or
        technique page. Updates ``sources`` and ``teachers`` lists.
      * ``referenced-by`` — bullet under ``## Referenced by`` on an
        instructor page (the named person was mentioned in a source's
        ``references`` field, not as a teaching authority). Updates
        ``references_count`` and ``references_count_sources``.
      * ``as-author`` — bullet under ``## Sources`` on an instructor
        page (the named person IS a teaching authority on this source,
        i.e. appears in the source's ``instructors`` field). Updates
        ``sources``, ``sources_count``, ``concepts_taught``,
        ``techniques_taught``.

    ``raw_slug`` is the slug that the contribution's source term would
    have produced WITHOUT vocab canonicalization — e.g., ``anchor-steps``
    when ``page_slug`` is ``anchor-step``. Recorded on the canonical
    page's ``aliases:`` frontmatter so the variant is discoverable from
    the canonical page. ``None`` when the contribution doesn't go
    through vocab canonicalization (instructor pages).

    ``concepts_taught`` / ``techniques_taught`` carry the slugs of the
    concept/technique pages this same source contributed to under this
    instructor's name. Only populated on ``as-author`` contributions;
    apply_contributions merges them into the instructor page's
    frontmatter lists.
    """

    page_type: PageType
    page_slug: str
    teacher: str | None
    paragraph_md: str
    source_slug: str
    source_bucket: str
    kind: Literal["by-teacher", "referenced-by", "as-author"] = "by-teacher"
    raw_slug: str | None = None
    # Optional payloads for richer page sections:
    common_mistakes: list[tuple[str, str]] = field(default_factory=list)
    concepts_taught: list[str] = field(default_factory=list)
    techniques_taught: list[str] = field(default_factory=list)


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


# Rendered into ``## By teacher`` paragraphs when the upstream LLM
# extracted a concept/technique name but no ``detail`` / ``description``
# prose to go with it. Better than the old ``(Sugar Push). [citation]``
# stub because the absence of elaboration is now explicit on the page,
# and the bullet still counts toward the source-count synthesis.
_EMPTY_DETAIL_PLACEHOLDER: str = "_Referenced without elaboration in source._"


def _format_teacher_paragraph(detail: str, source_bucket: str, source_slug: str) -> str:
    """Render one teacher's contribution paragraph for a derived page.

    Shape: ``<detail prose> <citation>``. Citation goes at the end so
    the prose reads naturally. Empty / whitespace-only detail renders
    the placeholder rather than echoing the term name in parentheses,
    so concept and technique pages don't accumulate ``(Sugar Push).``-
    style stub bullets.
    """
    detail = detail.strip()
    citation = _source_citation(source_bucket, source_slug)
    if not detail:
        return f"{_EMPTY_DETAIL_PLACEHOLDER} {citation}"
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
    concept_aliases: AliasMap | None = None,
    technique_aliases: AliasMap | None = None,
    instructor_aliases: AliasMap | None = None,
    session_date: Any | None = None,
    session_type: str | None = None,
    title: str | None = None,
) -> list[Contribution]:
    """Walk notes_json deterministically and return all the contributions.

    Pure function — doesn't touch disk. Caller invokes
    ``apply_contributions`` to write them out.

    Attribution rule (per CLAUDE.md "Multi-instructor sources"): each
    contribution is attributed to ALL canonical_instructors on the
    source, unless the data itself attributes a specific claim to a
    specific person (e.g. ``quotes[].speaker``). Defaults to all.

    Vocab canonicalization (per CLAUDE.md "Vocabulary handling"):

      * ``concept_aliases`` and ``technique_aliases`` are the loaded
        ``concepts/_aliases.yaml`` and ``techniques/_aliases.yaml`` maps.
        When provided, raw concept/term/pattern names route through
        ``canonicalize_concept_slug`` / ``canonicalize_technique_slug``
        before becoming a slug. The original slug form is recorded on
        the resulting Contribution's ``raw_slug`` so the canonical
        page's ``aliases:`` frontmatter can pick it up.
      * When ``None`` (legacy callers / tests), behavior reduces to the
        original ``slugify`` flow with no plural collapse and no alias
        lookup. Production callers always pass both.

    Instructor-as-author (per CLAUDE.md "Instructor pages"):

      * For each canonical instructor on the source, a Contribution
        with ``kind="as-author"`` is emitted targeting that instructor's
        page. The bullet that lands under ``## Sources`` describes this
        source. The contribution also carries the concept/technique
        slugs this same source contributed under that instructor's name
        so the instructor page's ``concepts_taught`` /
        ``techniques_taught`` lists stay in sync.
      * ``session_date`` / ``session_type`` / ``title`` are used to
        render the source bullet. When omitted, the bullet falls back
        to just the source slug + a link.

    Reference canonicalization:

      * ``instructor_aliases`` (the loaded ``instructors/_aliases.yaml``)
        is consulted when slugifying each ``references[].name``. Without
        this, manual cluster collapses Kaiano set up for the primary-
        authorship path (Benji Schwimmer mis-extractions → benji,
        Brandi/Brandy variants → brandi-guild) don't apply to the
        references codepath and the alias clusters reappear as separate
        referenced-by stubs.

    Skipped here (handled elsewhere or deferred):
    - ``student_observations`` — never enters the wiki, per spec.
    - ``action_items`` — never enters the wiki, per spec.
    - ``off_topic_notes`` / ``suggested_new_sections`` — operational
      signals only.
    - ``competition_notes`` — would route to a ``competition-strategy``
      concept page; deferred until we have enough to make it useful.
    """
    contributions: list[Contribution] = []

    def _concept_slug(name: str) -> tuple[str, str | None]:
        """Return ``(canonical_slug, raw_slug_if_distinct)``.

        ``raw_slug_if_distinct`` is the slugify-only form when it
        differs from the canonical (e.g. plural variant or aliased
        synonym), so the canonical page can record it as an alias.
        ``None`` when raw and canonical match.
        """
        raw = slugify(name)
        if not raw:
            return "", None
        if concept_aliases is None:
            return raw, None
        canonical = canonicalize_concept_slug(name, aliases=concept_aliases)
        if not canonical:
            return "", None
        return canonical, (raw if raw != canonical else None)

    def _technique_slug(name: str) -> tuple[str, str | None]:
        raw = slugify(name)
        if not raw:
            return "", None
        if technique_aliases is None:
            return raw, None
        canonical = canonicalize_technique_slug(name, aliases=technique_aliases)
        if not canonical:
            return "", None
        return canonical, (raw if raw != canonical else None)

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
        slug, raw_slug = _concept_slug(name)
        if not slug:
            continue
        para = _format_teacher_paragraph(detail, source_bucket, source_slug)
        for teacher in canonical_instructors or [None]:  # type: ignore[list-item]
            contributions.append(
                Contribution(
                    page_type="concept",
                    page_slug=slug,
                    teacher=teacher,
                    paragraph_md=para,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                    raw_slug=raw_slug,
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
        slug, raw_slug = _technique_slug(name)
        if not slug:
            continue
        para = _format_teacher_paragraph(description, source_bucket, source_slug)
        for teacher in canonical_instructors or [None]:  # type: ignore[list-item]
            contributions.append(
                Contribution(
                    page_type="technique",
                    page_slug=slug,
                    teacher=teacher,
                    paragraph_md=para,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                    raw_slug=raw_slug,
                )
            )

    # ── vocabulary_terms → concept pages (term as concept name) ─────
    # Per CLAUDE.md vocabulary rules: case-insensitive and plural
    # variants merge silently (handled by ``_concept_slug`` →
    # ``canonicalize_concept_slug``); manual entries in
    # ``concepts/_aliases.yaml`` collapse synonyms; everything else
    # stays its own page. Cross-instructor subtle-difference escalation
    # (terminology pages) is a separate pass — see ``reconcile``.
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
        slug, raw_slug = _concept_slug(term)
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
                    raw_slug=raw_slug,
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
        # Apply the instructor alias map so manual cluster collapses
        # (e.g., benji-schwimmer → benji) reach the references path
        # as well as the primary instructors path. The map's
        # ``to_slug`` re-normalizes the input via _normalize_key, so
        # passing the slug form is equivalent to passing the raw name.
        if instructor_aliases is not None:
            mapped = instructor_aliases.to_slug(slug)
            if mapped is not None:
                slug = mapped
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

    # ── canonical_instructors → instructor pages (as-author) ────────
    # For each teaching authority on this source, emit a contribution
    # to their instructor page's ``## Sources`` section. The bullet
    # describes the source (date + slug + session-type + title) and
    # the contribution carries the concept/technique slugs this same
    # source contributed under this instructor's name — those become
    # ``concepts_taught`` / ``techniques_taught`` on the instructor page.
    if canonical_instructors:
        # Compute per-instructor concept/technique attributions from the
        # by-teacher contributions we already planned above. Each
        # instructor is attributed to the slugs they teach on this source.
        per_author_concepts: dict[str, list[str]] = {
            ci: [] for ci in canonical_instructors
        }
        per_author_techniques: dict[str, list[str]] = {
            ci: [] for ci in canonical_instructors
        }
        for c in contributions:
            if c.kind != "by-teacher" or c.teacher is None:
                continue
            if c.teacher not in per_author_concepts:
                continue
            if (
                c.page_type == "concept"
                and c.page_slug not in per_author_concepts[c.teacher]
            ):
                per_author_concepts[c.teacher].append(c.page_slug)
            elif (
                c.page_type == "technique"
                and c.page_slug not in per_author_techniques[c.teacher]
            ):
                per_author_techniques[c.teacher].append(c.page_slug)

        bullet = _format_author_source_bullet(
            source_bucket=source_bucket,
            source_slug=source_slug,
            session_date=session_date,
            session_type=session_type,
            title=title,
        )
        for instructor in canonical_instructors:
            contributions.append(
                Contribution(
                    page_type="instructor",
                    page_slug=instructor,
                    teacher=None,
                    paragraph_md=bullet,
                    source_slug=source_slug,
                    source_bucket=source_bucket,
                    kind="as-author",
                    concepts_taught=per_author_concepts[instructor],
                    techniques_taught=per_author_techniques[instructor],
                )
            )

    return contributions


def _format_author_source_bullet(
    *,
    source_bucket: str,
    source_slug: str,
    session_date: Any | None,
    session_type: str | None,
    title: str | None,
) -> str:
    """Render the ``## Sources`` bullet for an instructor-as-author entry.

    Shape: ``- **YYYY-MM-DD** — [[sources/<bucket>/<slug>|<title>]] · <session-type>``

    Falls back gracefully when fields are missing — the wikilink is the
    only required part, since that's what makes the bullet useful as
    navigation.
    """
    # Date prefix
    if session_date is not None:
        date_str = str(session_date)
    else:
        date_str = ""

    # Wikilink with display text
    display = title.strip() if title and title.strip() else source_slug
    link = f"[[sources/{source_bucket}/{source_slug}|{display}]]"

    # Session-type label, lightly humanized
    type_label = ""
    if session_type:
        type_label = session_type.replace("_", " ").strip()

    parts: list[str] = []
    if date_str:
        parts.append(f"**{date_str}**")
        parts.append("—")
    parts.append(link)
    if type_label:
        parts.append(f"· {type_label}")

    return "- " + " ".join(parts)


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


def _upsert_source_bullet(
    page: md.Page,
    *,
    section_name: str,
    bullet_md: str,
    source_bucket: str,
    source_slug: str,
) -> None:
    """Append or replace a bullet under ``## <section_name>`` keyed by source.

    Used for instructor-page ``## Sources`` and ``## Referenced by``
    sections — both are flat bullet lists where each line cites one
    source page. Idempotent: re-applying the same source replaces the
    existing bullet rather than duplicating.
    """
    section = page.ensure_section(section_name)
    citation_link = f"sources/{source_bucket}/{source_slug}"
    bullet = bullet_md if bullet_md.startswith("- ") else f"- {bullet_md}"

    new_lines: list[str] = []
    replaced = False
    seen_bullet_block = False
    for line in section:
        stripped = line.strip()
        is_our_bullet = stripped.startswith("- ") and citation_link in stripped
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

    page.set_section(section_name, new_lines)


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
            elif c.kind == "as-author":
                # Instructor page, this person is a teaching authority
                # on the source. Lands as a bullet under ``## Sources``
                # and updates the page's authorship-facing frontmatter.
                _upsert_source_bullet(
                    page,
                    section_name="Sources",
                    bullet_md=c.paragraph_md,
                    source_bucket=c.source_bucket,
                    source_slug=c.source_slug,
                )
                md.add_unique(page.frontmatter, "sources", c.source_slug)
                # sources_count mirrors the distinct count of source
                # slugs (CLAUDE.md frontmatter spec).
                page.frontmatter["sources_count"] = len(
                    page.frontmatter.get("sources", [])
                )
                for concept_slug in c.concepts_taught:
                    md.add_unique(page.frontmatter, "concepts_taught", concept_slug)
                for technique_slug in c.techniques_taught:
                    md.add_unique(page.frontmatter, "techniques_taught", technique_slug)
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
                # Record the variant form that was collapsed into this
                # canonical slug (singular/plural or alias-map merge) on
                # the canonical page's ``aliases:`` frontmatter. The
                # canonical page becomes the discoverable home for any
                # variant naming someone might search for.
                if c.raw_slug and c.raw_slug != c.page_slug:
                    md.add_unique(page.frontmatter, "aliases", c.raw_slug)

        # Structural synthesis pass — populates ## Overview (if blank)
        # and re-renders ## Across sources from the now-up-to-date page.
        _synthesize_page(page, page_type)

        page_path.write_text(md.serialize(page))
        touched[page_path] = None

    return list(touched.keys())


# ── Structural synthesis: ## Overview + ## Across sources + status ─────
# Run after all contributions for a page are upserted. Three outputs,
# all purely derived from the page's current frontmatter + body:
#
#   ## Overview        — one neutral sentence summarizing how many
#                        teachers + sources contribute to this concept.
#                        Rendered when the page has crossed the
#                        >=3-source threshold.
#
#   ## Across sources  — per-teacher source counts, computed by
#                        scanning ## By teacher's H3 subsections for
#                        source citation tokens.
#
#   status (frontmatter) — ``stub`` below the threshold,
#                          ``draft`` at or above it.
#
# All three regenerate from current data on every ingest. The wiki is
# a purely-derived view of upstream notes_json; the only manual surface
# in this codebase is the alias maps. Nothing preserves prior section
# content across re-renders — preserving would just let stale outputs
# (the "3 sources by Kaiano and Amy" Overview written when only 3
# sources had contributed) survive subsequent ingests.

_MIN_SOURCES_FOR_SYNTHESIS: int = 3

# Matches the source-citation token rendered by _source_citation: e.g.
# "([[sources/kaiano/2025-09-15-kate-private-lesson]])". One match per
# paragraph in a teacher's subsection means one source contribution.
_CITATION_RE = re.compile(r"\(\[\[sources/[^/]+/([^\]]+)\]\]\)")


def _count_sources_per_teacher(by_teacher_body: list[str]) -> list[tuple[str, int]]:
    """Return [(teacher_heading, distinct_source_count), ...] in document order.

    Skips any prose preceding the first H3. A teacher subsection whose
    body has no source citations contributes (heading, 0) — we keep the
    entry so the Across-sources rendering matches By-teacher order.
    """
    chunks = md.split_h3(by_teacher_body)
    counts: list[tuple[str, int]] = []
    for heading, body in chunks:
        if heading is None:
            continue
        text = "\n".join(body)
        sources = set(_CITATION_RE.findall(text))
        counts.append((heading, len(sources)))
    return counts


def _render_overview(
    *, source_count: int, teacher_count: int, teachers_in_order: list[str]
) -> list[str]:
    """One-sentence neutral gloss for the ## Overview section.

    Kept deliberately mechanical so it's obvious to a reader that this
    is auto-generated metadata, not a synthesized definition. The
    sentence regenerates on every ingest from current frontmatter, so
    the count and teacher list always match the rest of the page.
    """
    if teacher_count == 1:
        teachers_clause = f"by {teachers_in_order[0]}"
    elif teacher_count == 2:
        teachers_clause = f"by {teachers_in_order[0]} and {teachers_in_order[1]}"
    else:
        teachers_clause = (
            f"by {', '.join(teachers_in_order[:-1])}, and {teachers_in_order[-1]}"
        )
    return [
        f"Taught across {source_count} sources {teachers_clause}. "
        "See **By teacher** below for each teacher's framing.",
    ]


def _render_across_sources(per_teacher: list[tuple[str, int]]) -> list[str]:
    """Bullet list of teachers and their source counts."""
    lines: list[str] = []
    for heading, n in per_teacher:
        if n <= 0:
            continue
        s = "source" if n == 1 else "sources"
        lines.append(f"- **{heading}** — {n} {s}")
    return lines


def _synthesize_page(page: md.Page, page_type: PageType) -> None:
    """Synthesize derived sections + status. Mutates ``page``.

    Three outputs, all purely derived from the page's current
    frontmatter and body. None preserve prior content across re-
    renders — the wiki is a derived view, the only manual editing
    surface is the alias maps.

      * Status (all derived page types): ``stub`` below the
        ``_MIN_SOURCES_FOR_SYNTHESIS`` (=3) threshold, ``draft`` at
        or above. ``mature`` is not auto-set; if and when a curator
        configuration surface for maturity lands it would set the
        value elsewhere and ``_synthesize_page`` would learn to
        respect it. For now status is a pure function of source count.
      * ``## Overview`` (concept/technique only): regenerated each
        run when the page has >=3 sources. Skipped below threshold —
        a single-source page doesn't have enough cross-source signal
        to summarize.
      * ``## Across sources`` (concept/technique only): per-teacher
        source-count bullets, regenerated each run when the page
        has >=3 sources.

    No-op on page types that don't carry these conventions.
    """
    if page_type not in {"concept", "technique", "instructor"}:
        return

    sources = page.frontmatter.get("sources") or []
    if not isinstance(sources, list):
        return

    # Status is a pure function of source count.
    if len(sources) >= _MIN_SOURCES_FOR_SYNTHESIS:
        page.frontmatter["status"] = "draft"
    else:
        page.frontmatter["status"] = "stub"
        return  # Below threshold: Overview and Across-sources are skipped.

    # The rest is concept/technique specific. Instructor pages don't
    # render Overview/Across-sources from per-teacher attribution —
    # their other sections (``## Background``, ``## Teaching
    # themes``) are slated for an LLM synthesis pass that doesn't
    # exist yet.
    if page_type == "instructor":
        return

    by_teacher = page.get_section("By teacher") or []
    per_teacher = _count_sources_per_teacher(by_teacher)
    if not per_teacher:
        return
    teachers_in_order = [h for h, _ in per_teacher]

    page.set_section(
        "Overview",
        _render_overview(
            source_count=len(sources),
            teacher_count=len(teachers_in_order),
            teachers_in_order=teachers_in_order,
        ),
    )
    page.set_section("Across sources", _render_across_sources(per_teacher))


# ── Backfill wipe ───────────────────────────────────────────────────────


# Files in derived-page directories that are NOT derived pages and must
# be preserved across a wipe-and-rebuild backfill. The alias maps are
# authored by Kaiano (concept/technique vocab judgment); anything else
# checked in here was added intentionally and shouldn't be silently
# blown away.
_DERIVED_DIR_PRESERVE_NAMES: frozenset[str] = frozenset(
    {
        "_aliases.yaml",
    }
)

# Directories under the wiki root that hold derived (curator-owned)
# pages. ``instructors`` is included so a re-backfill rebuilds
# instructor pages from current source state — but their ``_aliases.yaml``
# is preserved per the rule above.
_DERIVED_PAGE_DIRS: tuple[str, ...] = (
    "concepts",
    "techniques",
    "instructors",
    "terminology",
)


def wipe_derived_pages(wiki_repo_path: Path) -> list[Path]:
    """Delete all derived pages so a backfill rebuilds them from scratch.

    Removes every ``*.md`` under the four derived-page directories
    (``concepts/``, ``techniques/``, ``instructors/``, ``terminology/``)
    except the preserved ``_aliases.yaml`` files. Returns the absolute
    paths of files deleted so the caller can stage the removal in git.

    Safe to call on a fresh checkout (directories may not exist yet);
    missing directories are silently ignored. Safe to call repeatedly;
    subsequent calls find nothing to delete.

    The wiki is a derived artifact per CLAUDE.md ("It is not
    authoritative ... can be regenerated from scratch if needed"), so
    this is the canonical way to reset state before a full backfill
    that picks up vocab-collapse changes or any other behavior change
    that should reshape the derived layer.
    """
    removed: list[Path] = []
    for sub in _DERIVED_PAGE_DIRS:
        directory = wiki_repo_path / sub
        if not directory.is_dir():
            continue
        for child in directory.iterdir():
            if child.name in _DERIVED_DIR_PRESERVE_NAMES:
                continue
            if not child.is_file():
                continue
            if child.suffix != ".md":
                # Be conservative — only blow away markdown. Anything
                # else (a stray .yaml that isn't an alias map, a
                # README, an image) is preserved.
                continue
            child.unlink()
            removed.append(child)
    return removed


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
