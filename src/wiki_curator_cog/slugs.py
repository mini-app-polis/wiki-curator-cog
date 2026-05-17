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
