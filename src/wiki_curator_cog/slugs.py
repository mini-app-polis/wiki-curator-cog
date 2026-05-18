"""Slug derivation, name canonicalization, and source bucketing.

Pure functions called by ``curator.ingest_one_source``. All behavior here
is deterministic — no IO, no LLM, no API calls. Kept in its own module so
the rules are independently testable and the curator's orchestration
code stays focused on sequencing.

The three responsibilities:

  1. ``canonicalize_name`` — translate an upstream raw name into the
     wiki's canonical slug via the AliasMap. In backfill/automated mode
     unknown names are auto-added as ``<slug>: <slug>`` (per
     wcs-wiki/CLAUDE.md "Name canonicalization"); in interactive mode
     the caller decides how to handle the unknown.

  2. ``bucket_for_instructors`` — pick the ``sources/<bucket>/``
     directory for a source. Alphabetically-first match against the
     known buckets (``BUCKETED_INSTRUCTORS``), or ``external`` if none
     of the canonical instructors are bucketed.

  3. ``build_source_slug`` — construct the source page's filename stem
     in ``YYYY-MM-DD-<rest>`` form. Uses the ``title`` field when
     present; otherwise falls back to
     ``<primary-instructor>-<session-type>`` per CLAUDE.md "Slug and
     naming conventions".
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable

from .aliases import AliasMap, _normalize_key

# Sources directories that exist under sources/. Anything else falls
# into sources/external/. The bucket list lives here (not in the
# config) because it's a wiki schema decision, not a deployment knob —
# CLAUDE.md "Repository layout" documents it. Sorted alphabetically so
# bucket selection is unambiguous.
BUCKETED_INSTRUCTORS: tuple[str, ...] = ("kaiano", "kate", "robert")
EXTERNAL_BUCKET: str = "external"


class UnknownNameError(LookupError):
    """Raised when a raw name can't be resolved and auto-add is disabled.

    Interactive mode catches this and prompts Kaiano; automated and
    backfill modes never raise it because they pass ``auto_add=True``.
    """

    def __init__(self, raw_name: str) -> None:
        super().__init__(
            f"Name {raw_name!r} is not in the alias map. "
            "Pass auto_add=True to add it as <slug>: <slug>, or "
            "edit instructors/_aliases.yaml manually."
        )
        self.raw_name = raw_name


def canonicalize_name(
    raw_name: str,
    aliases: AliasMap,
    *,
    auto_add: bool,
) -> str:
    """Resolve a raw upstream name to a canonical wiki slug.

    Mutates ``aliases`` in place when a new name is auto-added; the
    caller is responsible for ``aliases.save()`` at an appropriate
    boundary (typically once at end of run, per the per-source commit
    grouping decision).
    """
    existing = aliases.to_slug(raw_name)
    if existing is not None:
        return existing
    if not auto_add:
        raise UnknownNameError(raw_name)

    # Auto-add: the canonical slug is the normalized form of the raw
    # name. The alias map key and value end up the same string.
    canonical = _normalize_key(raw_name)
    if not canonical:
        # Pathological input ("---", whitespace, punctuation-only) that
        # normalizes to empty. Refuse to add an empty key; the caller
        # should surface this as a source quality issue.
        raise ValueError(f"Name {raw_name!r} normalizes to empty; cannot auto-add.")
    aliases.add(raw_name, canonical)
    return canonical


def canonicalize_names(
    raw_names: Iterable[str],
    aliases: AliasMap,
    *,
    auto_add: bool,
) -> list[str]:
    """Canonicalize a list of raw names, preserving order.

    Drops empty/whitespace-only entries silently. Duplicates are
    preserved — the source page records what the upstream record said,
    even if the same person appears twice.
    """
    result: list[str] = []
    for raw in raw_names:
        if not raw or not str(raw).strip():
            continue
        result.append(canonicalize_name(str(raw), aliases, auto_add=auto_add))
    return result


def bucket_for_instructors(canonical_instructors: list[str]) -> str:
    """Pick the sources/<bucket>/ directory for a source page.

    Per CLAUDE.md "Sources bucketing rule": the alphabetically-first
    bucketed instructor wins. If none of the canonical instructors are
    in ``BUCKETED_INSTRUCTORS``, the source files under ``external``.
    Empty input also falls to ``external`` (defensive — upstream notes
    should always carry at least one instructor, but we don't crash on
    a malformed record).
    """
    candidates = sorted(
        slug for slug in canonical_instructors if slug in BUCKETED_INSTRUCTORS
    )
    if candidates:
        return candidates[0]
    return EXTERNAL_BUCKET


# Slugs become filename components, and most filesystems cap
# individual components at 255 bytes. We cap well below that so the
# `.md` extension, any disambiguator suffix (``-2``, ``-3``), and any
# wikilink formatting all fit comfortably. The upstream LLM
# occasionally emits a full procedural description in a
# ``key_concept.concept`` field where a short noun-phrase is expected
# (e.g. "Watch-hover-touch-lead drill: followers first demonstrate
# their own version of a pattern, then leaders hover without
# touching..."); rather than fail the ingest with ENAMETOOLONG, we
# truncate. 80 chars is short enough to be safe across exotic
# filesystems and long enough to retain useful semantic content.
_SLUG_MAX_LEN: int = 80


def slugify(text: str) -> str:
    """Generic slugifier for free-form strings (e.g., note titles).

    Lowercase, collapse whitespace and underscores to single hyphens,
    drop characters outside ``[a-z0-9-]``. Multiple consecutive hyphens
    are collapsed; leading/trailing hyphens are trimmed. The result is
    capped at ``_SLUG_MAX_LEN`` chars, truncating at the last hyphen
    boundary so we never end mid-word.

    Distinct from ``aliases._normalize_key`` only in name — the rules
    are identical (modulo the length cap). Reused here under a
    friendlier alias to keep the intent clear at call sites.
    """
    s = text.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if len(s) > _SLUG_MAX_LEN:
        truncated = s[:_SLUG_MAX_LEN]
        last_hyphen = truncated.rfind("-")
        # Only fall back to the hard cut if there's no hyphen at all
        # in the truncated window (pathological — would mean an 80-char
        # word with no spaces, which slugify wouldn't produce).
        if last_hyphen > 0:
            s = truncated[:last_hyphen]
        else:
            s = truncated
    return s


def _session_type_slug(session_type: str) -> str:
    """``private_lesson`` → ``private-lesson``. Tolerant of unknown values."""
    return slugify(session_type.replace("_", "-"))


# ── Vocabulary canonicalization (concept / technique slugs) ─────────────
#
# Concept and technique pages use a two-stage canonicalization that
# mirrors what the instructor alias map does for people:
#
#   1. ``slugify`` puts the raw term into the wiki's slug form
#      (lowercase, hyphenated, ASCII-only).
#   2. ``_depluralize_slug`` collapses trailing-``s``/``-es``/``-ies``
#      so ``anchor-step`` and ``anchor-steps`` map to one canonical.
#   3. The vocab alias map (``concepts/_aliases.yaml`` /
#      ``techniques/_aliases.yaml``) collapses everything else —
#      synonyms, abbreviations, reframings.
#
# Stages 1–2 are mechanical and apply silently. Stage 3 is the
# judgment-call layer Kaiano controls by editing the alias map; the
# curator never auto-adds entries here (unlike the instructor map,
# where auto-add is safe because identity is established upstream).
#
# Per CLAUDE.md "Vocabulary handling" rule 6 ("When in doubt, prefer a
# terminology page over a silent merge"), the alias maps default to
# empty and silent merges only happen for the case/plural variants
# stages 1–2 catch.

# Plural endings handled by ``_depluralize_slug``. Order matters:
# longest suffix first so ``-ies`` is tried before ``-s``.
#
# We deliberately don't handle irregulars (``feet`` ↔ ``foot``,
# ``children`` ↔ ``child``). WCS vocabulary doesn't surface them often
# enough to justify the false-positive risk on unrelated words.
_PLURAL_RULES: tuple[tuple[str, str], ...] = (
    # ``-ies`` → ``-y``: ``policies`` → ``policy``. Skip when the stem
    # before -ies is only 1 char (``dies`` → ``dy`` is wrong; ``ties``
    # is also borderline but rare in this corpus).
    ("ies", "y"),
    # ``-es`` after sibilants: ``boxes`` → ``box``, ``pushes`` →
    # ``push``, ``wishes`` → ``wish``. We strip the ``-es`` (not
    # ``-s``) because the ``e`` is just a phonetic insert for the
    # sibilant ending.
    ("ches", "ch"),
    ("shes", "sh"),
    ("sses", "ss"),
    ("xes", "x"),
    ("zes", "z"),
    # Plain ``-s`` plurals. The most common case; also the trickiest
    # because it false-positives on words that just end in ``s``
    # (``bus``, ``focus``, ``axis``). We mitigate by refusing to strip
    # when the resulting stem would be ≤2 chars OR would end in
    # ``s``/``ss`` (so ``stress`` doesn't become ``stres``, ``bus``
    # stays ``bus``, ``mass`` stays ``mass``).
    ("s", ""),
)

# Minimum length of the stem AFTER stripping a ``-s``. Below this we
# refuse to depluralize, since the word is too short to safely assume
# we're looking at a plural.
_MIN_DEPLURALIZED_STEM: int = 3


def _depluralize_slug(slug: str) -> str:
    """Collapse trailing plural suffixes in a slug. Safe for non-plurals.

    Operates on the slug's last token (after the final hyphen) so that
    compound slugs like ``anchor-steps`` collapse to ``anchor-step``
    rather than crashing into the hyphen boundary. The earlier tokens
    are preserved verbatim.

    Returns the input unchanged when no rule applies or when applying a
    rule would produce a stem below ``_MIN_DEPLURALIZED_STEM``. The
    function is total — every string maps to some string.
    """
    if not slug:
        return slug

    # Operate on the last hyphen-separated token only. The lead is the
    # rest of the slug; if there's no hyphen, the lead is empty and
    # ``last`` is the whole slug.
    last_hyphen = slug.rfind("-")
    if last_hyphen == -1:
        lead, last = "", slug
    else:
        lead, last = slug[: last_hyphen + 1], slug[last_hyphen + 1 :]

    for suffix, replacement in _PLURAL_RULES:
        if not last.endswith(suffix):
            continue
        stem = last[: len(last) - len(suffix)] + replacement
        # Refuse if the resulting stem is too short — short words ending
        # in -s are usually not plurals (``bus``, ``gas``, ``yes``).
        if len(stem) < _MIN_DEPLURALIZED_STEM:
            continue
        # For the plain ``-s`` rule, also refuse if the underlying word
        # ended in ``-ss`` (``stress``, ``mass``, ``loss``) so we don't
        # convert it to a non-plural sibling.
        if suffix == "s" and stem.endswith("s"):
            continue
        return lead + stem

    return slug


def canonicalize_concept_slug(text: str, *, aliases) -> str:  # type: ignore[no-untyped-def]
    """Turn a raw concept/vocabulary string into its canonical wiki slug.

    Pipeline:

      1. ``slugify`` to ASCII lowercase-hyphenated form.
      2. ``_depluralize_slug`` to collapse trailing plural suffixes.
      3. Alias-map lookup against ``aliases`` (typically the loaded
         ``concepts/_aliases.yaml``). Returns the canonical slug if the
         depluralized form is mapped, else the depluralized form
         itself.

    The alias map's ``to_slug`` accepts a raw string and normalizes
    internally, so we can pass the slug form directly. Tried against
    BOTH the depluralized and the original slug — covers the case
    where someone manually mapped ``anchor-steps: anchor-step`` and
    expects it to win over the silent depluralization.

    Returns an empty string when the input slugifies to empty; callers
    should treat that as "skip this contribution".
    """
    raw = slugify(text)
    if not raw:
        return ""
    stemmed = _depluralize_slug(raw)
    # Try raw first so a manual alias for the plural form beats the
    # depluralization rule. Then try the stemmed form.
    mapped = aliases.to_slug(raw)
    if mapped is None:
        mapped = aliases.to_slug(stemmed)
    return mapped if mapped is not None else stemmed


def canonicalize_technique_slug(text: str, *, aliases) -> str:  # type: ignore[no-untyped-def]
    """Like ``canonicalize_concept_slug`` but for techniques.

    Behaviorally identical — separated by name so callers express
    which alias map they intend to consult. Keeps the call sites
    self-documenting and lets us specialize later if the rules diverge.
    """
    return canonicalize_concept_slug(text, aliases=aliases)


def build_source_slug(
    *,
    session_date: dt.date | None,
    title: str | None,
    canonical_instructors: list[str],
    session_type: str,
    created_at: dt.datetime,
) -> str:
    """Build the source page filename stem.

    Pattern: ``YYYY-MM-DD-<rest>`` where ``<rest>`` is:

    - the slugified ``title`` if present and non-empty, otherwise
    - ``<primary-instructor>-<session-type>`` — e.g.
      ``kate-private-lesson``, ``kaiano-group-class``.

    ``session_date`` is the preferred date prefix; we fall back to
    ``created_at.date()`` when it's missing (rare but possible since
    the upstream model declares it optional).

    Primary instructor = the alphabetically-first canonical instructor
    when multiple are present. We deliberately don't use the bucket
    name here — a workshop with ``[robert, kate]`` is bucketed under
    ``kate/`` but its slug should mention ``kate`` for the same reason.
    For external-bucket sources, the slug includes whichever
    non-bucketed instructor name was first; this loses a little
    information but keeps filenames stable across re-ingests.
    """
    date_prefix = (session_date or created_at.date()).isoformat()

    if title and title.strip():
        rest = slugify(title)
        if rest:
            return f"{date_prefix}-{rest}"
        # If a title was present but slugified to nothing (all-punct
        # title or similar), fall through to the instructor-based form
        # rather than emitting ``YYYY-MM-DD-`` with a dangling hyphen.

    primary = sorted(canonical_instructors)[0] if canonical_instructors else "unknown"
    return f"{date_prefix}-{primary}-{_session_type_slug(session_type)}"
