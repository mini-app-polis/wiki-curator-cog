"""Tests for slug derivation, name canonicalization, and bucketing."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from wiki_curator_cog.aliases import AliasMap
from wiki_curator_cog.slugs import (
    BUCKETED_INSTRUCTORS,
    EXTERNAL_BUCKET,
    UnknownNameError,
    bucket_for_instructors,
    build_source_slug,
    canonicalize_name,
    canonicalize_names,
    slugify,
)

# ── canonicalize_name ───────────────────────────────────────────────────


def test_canonicalize_name_returns_existing_slug(wiki_repo_path: Path) -> None:
    aliases_file = wiki_repo_path / "instructors" / "_aliases.yaml"
    aliases_file.write_text("kate: kate\nkate-b: kate\n")
    aliases = AliasMap.load(wiki_repo_path)

    assert canonicalize_name("Kate", aliases, auto_add=False) == "kate"
    assert canonicalize_name("kate b", aliases, auto_add=False) == "kate"


def test_canonicalize_name_raises_when_unknown_and_not_auto_add(
    wiki_repo_path: Path,
) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    with pytest.raises(UnknownNameError) as exc:
        canonicalize_name("Sarah", aliases, auto_add=False)
    assert exc.value.raw_name == "Sarah"


def test_canonicalize_name_auto_adds_when_unknown(wiki_repo_path: Path) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    slug = canonicalize_name("Sarah", aliases, auto_add=True)
    assert slug == "sarah"
    assert aliases.to_slug("Sarah") == "sarah"
    assert aliases.to_slug("SARAH") == "sarah"


def test_canonicalize_name_auto_add_uses_normalized_form(
    wiki_repo_path: Path,
) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    slug = canonicalize_name("Kaiano Levine", aliases, auto_add=True)
    assert slug == "kaiano-levine"


def test_canonicalize_name_refuses_empty_normalized(wiki_repo_path: Path) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    with pytest.raises(ValueError):
        canonicalize_name("---", aliases, auto_add=True)


# ── canonicalize_names ──────────────────────────────────────────────────


def test_canonicalize_names_preserves_order(wiki_repo_path: Path) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    out = canonicalize_names(["Kaiano", "Kate", "Robert"], aliases, auto_add=True)
    assert out == ["kaiano", "kate", "robert"]


def test_canonicalize_names_drops_empty_entries(wiki_repo_path: Path) -> None:
    aliases = AliasMap.load(wiki_repo_path)
    out = canonicalize_names(["Kate", "", "  ", None], aliases, auto_add=True)  # type: ignore[list-item]
    assert out == ["kate"]


# ── bucket_for_instructors ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "instructors,expected",
    [
        (["kate"], "kate"),
        (["kaiano"], "kaiano"),
        (["robert"], "robert"),
        (["robert", "kate"], "kate"),  # alphabetically first
        (["kaiano", "kate"], "kaiano"),  # alphabetically first
        (["kaiano", "robert", "kate"], "kaiano"),
        (["sarah"], EXTERNAL_BUCKET),
        (["sarah", "alex"], EXTERNAL_BUCKET),
        ([], EXTERNAL_BUCKET),
        (["kate", "sarah"], "kate"),  # bucketed wins over non-bucketed
    ],
)
def test_bucket_for_instructors(instructors: list[str], expected: str) -> None:
    assert bucket_for_instructors(instructors) == expected


def test_bucketed_instructors_is_sorted() -> None:
    # Ordering of this tuple should be alphabetical so behavior is
    # obvious to readers.
    assert list(BUCKETED_INSTRUCTORS) == sorted(BUCKETED_INSTRUCTORS)


# ── slugify ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Anchor step quality", "anchor-step-quality"),
        ("  Anchor   step  ", "anchor-step"),
        ("Anchor_step_quality", "anchor-step-quality"),
        ("Q4 prep!", "q4-prep"),
        ("Foo // Bar", "foo-bar"),
        ("--hello--", "hello"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_caps_excessively_long_input() -> None:
    """Survive the upstream LLM emitting a sentence as a concept name.

    Real corpus example that previously crashed with ENAMETOOLONG:
    "Watch-hover-touch-lead drill: followers first demonstrate their
    own version of a pattern, then leaders hover without touching..."
    """
    long_phrase = (
        "Watch-hover-touch-lead drill: followers first demonstrate "
        "their own version of a pattern, then leaders hover without "
        "touching, then leaders touch but followers still self-generate, "
        "then leaders fully lead — calibrating the lead to the "
        "follower's natural movement."
    )
    out = slugify(long_phrase)
    assert len(out) <= 80
    # Truncated at a hyphen boundary, so never ends mid-word.
    assert not out.endswith("-")
    # Still meaningful — beginning of the phrase preserved.
    assert out.startswith("watch-hover-touch-lead")


# ── build_source_slug ───────────────────────────────────────────────────


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC)


def test_build_source_slug_with_title() -> None:
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title="Anchor step quality",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    assert slug == "2025-09-15-anchor-step-quality"


def test_build_source_slug_without_title_falls_back_to_instructor_type() -> None:
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title=None,
        canonical_instructors=["kate"],
        session_type="private_lesson",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    assert slug == "2025-09-15-kate-private-lesson"


def test_build_source_slug_empty_title_falls_back() -> None:
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title="   ",
        canonical_instructors=["kaiano"],
        session_type="group_class",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    assert slug == "2025-09-15-kaiano-group-class"


def test_build_source_slug_uses_alpha_first_instructor_for_fallback() -> None:
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title=None,
        canonical_instructors=["robert", "kaiano"],
        session_type="other",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    # kaiano sorts before robert
    assert slug == "2025-09-15-kaiano-other"


def test_build_source_slug_missing_session_date_uses_created_at() -> None:
    slug = build_source_slug(
        session_date=None,
        title="Footwork",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        created_at=_ts("2025-10-01T08:30:00"),
    )
    assert slug == "2025-10-01-footwork"


def test_build_source_slug_unslugifiable_title_falls_back() -> None:
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title="!!!",
        canonical_instructors=["kate"],
        session_type="private_lesson",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    assert slug == "2025-09-15-kate-private-lesson"


def test_build_source_slug_no_instructors() -> None:
    # Degenerate but possible: a source with no instructors at all. We
    # don't crash; we use a placeholder primary instructor.
    slug = build_source_slug(
        session_date=dt.date(2025, 9, 15),
        title=None,
        canonical_instructors=[],
        session_type="other",
        created_at=_ts("2025-09-15T12:00:00"),
    )
    assert slug == "2025-09-15-unknown-other"
