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
    _depluralize_slug,
    bucket_for_instructors,
    build_source_slug,
    canonicalize_concept_slug,
    canonicalize_name,
    canonicalize_names,
    canonicalize_technique_slug,
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


# ── _depluralize_slug ───────────────────────────────────────────────────


def test_depluralize_strips_trailing_s() -> None:
    assert _depluralize_slug("anchor-steps") == "anchor-step"
    assert _depluralize_slug("whips") == "whip"
    assert _depluralize_slug("variations") == "variation"


def test_depluralize_handles_ies_to_y() -> None:
    assert _depluralize_slug("policies") == "policy"


def test_depluralize_handles_sibilant_es() -> None:
    assert _depluralize_slug("boxes") == "box"
    assert _depluralize_slug("pushes") == "push"
    assert _depluralize_slug("wishes") == "wish"


def test_depluralize_preserves_double_s_endings() -> None:
    # ``stress``, ``mass``, ``loss`` are not plurals; depluralizing
    # would corrupt them.
    assert _depluralize_slug("stress") == "stress"
    assert _depluralize_slug("compress") == "compress"


def test_depluralize_refuses_short_stems() -> None:
    # Too-short stems suggest the trailing ``s`` is part of the word,
    # not a plural marker.
    assert _depluralize_slug("bus") == "bus"
    assert _depluralize_slug("yes") == "yes"


def test_depluralize_refuses_vowel_terminal_stems() -> None:
    """Real English plural -s overwhelmingly lands on consonant stems.
    Words ending in -us, -is, -os are almost always singulars whose
    -s is part of the noun (focus, axis, rhinos as a singular adj
    rare; mostly nouns we shouldn't strip). The vowel-end guard
    catches these without an enumerated false-friend list."""
    # -us false friends.
    assert _depluralize_slug("focus") == "focus"
    assert _depluralize_slug("bonus") == "bonus"
    assert _depluralize_slug("campus") == "campus"
    assert _depluralize_slug("virus") == "virus"
    # -is false friends.
    assert _depluralize_slug("axis") == "axis"
    assert _depluralize_slug("basis") == "basis"
    assert _depluralize_slug("thesis") == "thesis"
    assert _depluralize_slug("tennis") == "tennis"
    # -os false friends.
    assert _depluralize_slug("rhinos") == "rhinos"
    # Compound slugs where the last token is a false friend.
    assert (
        _depluralize_slug("basic-whip-with-rotation-focus")
        == "basic-whip-with-rotation-focus"
    )
    # The guard doesn't block legitimate consonant-end plurals.
    assert _depluralize_slug("cats") == "cat"
    assert _depluralize_slug("pushes") == "push"
    assert _depluralize_slug("variations") == "variation"


def test_depluralize_operates_on_last_token_only() -> None:
    # The hyphen-separated lead must be preserved verbatim — only the
    # final token gets the plural collapse applied.
    assert _depluralize_slug("sugar-pushes") == "sugar-push"
    assert _depluralize_slug("anchor-step-variations") == "anchor-step-variation"


def test_depluralize_idempotent_on_singular() -> None:
    # Already-singular forms must round-trip unchanged.
    assert _depluralize_slug("anchor-step") == "anchor-step"
    assert _depluralize_slug("whip") == "whip"


def test_depluralize_handles_empty_input() -> None:
    assert _depluralize_slug("") == ""


# ── canonicalize_concept_slug / canonicalize_technique_slug ─────────────


def test_canonicalize_concept_slug_applies_plural_collapse(
    wiki_repo_path: Path,
) -> None:
    # No alias map entries — the depluralizer carries the merge.
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "_aliases.yaml").write_text("")
    aliases = AliasMap.load(wiki_repo_path, relative_dir="concepts")

    assert canonicalize_concept_slug("Anchor step", aliases=aliases) == "anchor-step"
    assert canonicalize_concept_slug("Anchor steps", aliases=aliases) == "anchor-step"
    assert canonicalize_concept_slug("ANCHOR STEPS", aliases=aliases) == "anchor-step"


def test_canonicalize_concept_slug_applies_alias_map(wiki_repo_path: Path) -> None:
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "_aliases.yaml").write_text(
        "anchor: anchor-step\nanchoring-action: anchor-step\n"
    )
    aliases = AliasMap.load(wiki_repo_path, relative_dir="concepts")

    assert canonicalize_concept_slug("Anchor", aliases=aliases) == "anchor-step"
    assert (
        canonicalize_concept_slug("anchoring action", aliases=aliases) == "anchor-step"
    )
    # Unmapped + depluralized: still works.
    assert canonicalize_concept_slug("Frame", aliases=aliases) == "frame"


def test_canonicalize_concept_slug_alias_wins_over_depluralization(
    wiki_repo_path: Path,
) -> None:
    # A manual alias entry for the plural form should beat the silent
    # depluralization rule.
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "_aliases.yaml").write_text(
        "anchor-steps: settle\n"  # contrived to make the test direction obvious
    )
    aliases = AliasMap.load(wiki_repo_path, relative_dir="concepts")

    assert canonicalize_concept_slug("Anchor steps", aliases=aliases) == "settle"


def test_canonicalize_concept_slug_returns_empty_for_empty_input(
    wiki_repo_path: Path,
) -> None:
    aliases = AliasMap.load(wiki_repo_path, relative_dir="concepts")
    assert canonicalize_concept_slug("", aliases=aliases) == ""
    assert canonicalize_concept_slug("   ", aliases=aliases) == ""


def test_canonicalize_technique_slug_behaves_like_concept_slug(
    wiki_repo_path: Path,
) -> None:
    (wiki_repo_path / "techniques").mkdir(exist_ok=True)
    (wiki_repo_path / "techniques" / "_aliases.yaml").write_text(
        "basic-whip: whip\nwhip-basic: whip\n"
    )
    aliases = AliasMap.load(wiki_repo_path, relative_dir="techniques")

    assert canonicalize_technique_slug("Basic whip", aliases=aliases) == "whip"
    assert canonicalize_technique_slug("whip basic", aliases=aliases) == "whip"
    assert canonicalize_technique_slug("Whips", aliases=aliases) == "whip"


# ── AliasMap relative_dir parameter ─────────────────────────────────────


def test_aliasmap_load_supports_relative_dir(wiki_repo_path: Path) -> None:
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "techniques").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "_aliases.yaml").write_text("foo: bar\n")
    (wiki_repo_path / "techniques" / "_aliases.yaml").write_text("baz: qux\n")

    concepts = AliasMap.load(wiki_repo_path, relative_dir="concepts")
    techniques = AliasMap.load(wiki_repo_path, relative_dir="techniques")
    # Default (no kwarg) still loads instructors/.
    instructors = AliasMap.load(wiki_repo_path)

    assert concepts.to_slug("foo") == "bar"
    assert techniques.to_slug("baz") == "qux"
    # Independent maps — concepts entry doesn't leak into instructors.
    assert instructors.to_slug("foo") is None
