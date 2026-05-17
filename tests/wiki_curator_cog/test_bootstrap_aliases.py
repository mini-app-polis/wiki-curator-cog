"""Tests for the one-time alias-bootstrap utility."""

from __future__ import annotations

from pathlib import Path

import yaml

from wiki_curator_cog.bootstrap_aliases import (
    _levenshtein,
    _load_instructor_pages,
    bootstrap_aliases,
    generate_proposals,
)

# ── helpers ────────────────────────────────────────────────────────────


def _write_instructor(
    wiki_repo_path: Path,
    slug: str,
    *,
    name: str | None = None,
    sources_count: int = 0,
    references_count: int = 0,
) -> None:
    fm = [
        "---",
        "type: instructor",
        f"slug: {slug}",
        f"name: {name or slug}",
        f"sources_count: {sources_count}",
        f"references_count: {references_count}",
        "concepts_taught: []",
        "techniques_taught: []",
        "status: stub",
        "---",
        "",
    ]
    (wiki_repo_path / "instructors" / f"{slug}.md").write_text("\n".join(fm))


# ── _levenshtein sanity ────────────────────────────────────────────────


def test_levenshtein_basic() -> None:
    assert _levenshtein("kitten", "sitting") == 3
    assert _levenshtein("benji-schwimmer", "benji-schremmer") == 2
    assert _levenshtein("a", "a") == 0
    assert _levenshtein("", "abc") == 3


# ── load_instructor_pages ──────────────────────────────────────────────


def test_load_instructor_pages_ignores_aliases_file(wiki_repo_path: Path) -> None:
    _write_instructor(wiki_repo_path, "kate", sources_count=5)
    _write_instructor(wiki_repo_path, "robert", sources_count=2)
    pages = _load_instructor_pages(wiki_repo_path)
    slugs = {p.slug for p in pages}
    assert slugs == {"kate", "robert"}


# ── proposal generation ────────────────────────────────────────────────


def test_first_token_safe_merge_single_expansion(wiki_repo_path: Path) -> None:
    """`kate` + `kate-benson` (only one kate-*) → merge into kate-benson."""
    _write_instructor(wiki_repo_path, "kate", sources_count=0, references_count=1)
    _write_instructor(
        wiki_repo_path, "kate-benson", sources_count=5, references_count=0
    )
    pages = _load_instructor_pages(wiki_repo_path)
    proposals = generate_proposals(pages)
    safe = [p for p in proposals if p.safe]
    assert len(safe) == 1
    assert safe[0].canonical == "kate-benson"
    assert safe[0].variants == ["kate"]


def test_first_token_ambiguous_multiple_expansions(wiki_repo_path: Path) -> None:
    """`kate` + `kate-benson` + `kate-marsden` → ambiguous, can't auto-merge."""
    _write_instructor(wiki_repo_path, "kate")
    _write_instructor(wiki_repo_path, "kate-benson", sources_count=5)
    _write_instructor(wiki_repo_path, "kate-marsden", sources_count=3)
    pages = _load_instructor_pages(wiki_repo_path)
    proposals = generate_proposals(pages)
    ambiguous = [p for p in proposals if not p.safe]
    assert len(ambiguous) >= 1
    assert set(ambiguous[0].variants) == {"kate", "kate-benson", "kate-marsden"}


def test_fuzzy_merge_misspellings_pick_most_popular(wiki_repo_path: Path) -> None:
    """benji-schwimmer (popular) + benji-schremmer + benji-schumer → merge into schwimmer."""
    _write_instructor(wiki_repo_path, "benji-schwimmer", sources_count=8)
    _write_instructor(wiki_repo_path, "benji-schremmer", sources_count=1)
    _write_instructor(wiki_repo_path, "benji-schumer", sources_count=2)
    pages = _load_instructor_pages(wiki_repo_path)
    proposals = generate_proposals(pages)
    safe = [p for p in proposals if p.safe]
    assert len(safe) == 1
    assert safe[0].canonical == "benji-schwimmer"
    assert set(safe[0].variants) == {"benji-schremmer", "benji-schumer"}


def test_distinct_people_with_shared_first_name_are_not_merged(
    wiki_repo_path: Path,
) -> None:
    """`brandi-gill` and `brandi-shanks` differ too much to be the same person."""
    _write_instructor(wiki_repo_path, "brandi-gill", sources_count=3)
    _write_instructor(wiki_repo_path, "brandi-shanks", sources_count=2)
    pages = _load_instructor_pages(wiki_repo_path)
    proposals = generate_proposals(pages)
    # No safe merge proposal grouping these together — edit distance > 2.
    for p in proposals:
        if p.safe:
            assert not (
                "brandi-gill" in p.variants + [p.canonical]
                and "brandi-shanks" in p.variants + [p.canonical]
            )


# ── end-to-end ─────────────────────────────────────────────────────────


def test_bootstrap_writes_aliases_and_review(wiki_repo_path: Path) -> None:
    _write_instructor(wiki_repo_path, "kate")
    _write_instructor(wiki_repo_path, "kate-benson", sources_count=5)
    _write_instructor(wiki_repo_path, "kate-marsden", sources_count=3)
    _write_instructor(wiki_repo_path, "benji-schwimmer", sources_count=8)
    _write_instructor(wiki_repo_path, "benji-schremmer", sources_count=1)
    _write_instructor(wiki_repo_path, "robert", sources_count=10)  # no merges needed

    result = bootstrap_aliases(wiki_repo_path)

    # Safe merges should include the benji cluster.
    aliases_path = wiki_repo_path / "instructors" / "_aliases.yaml"
    aliases_text = aliases_path.read_text()
    # Strip header comment to read YAML body cleanly.
    body_start = 0
    for line in aliases_text.splitlines():
        if not line.strip().startswith("#") and line.strip():
            break
        body_start += len(line) + 1
    aliases_map = yaml.safe_load(aliases_text[body_start:]) or {}
    assert aliases_map.get("benji-schremmer") == "benji-schwimmer"
    assert aliases_map.get("benji-schwimmer") == "benji-schwimmer"

    # Ambiguous Kate cluster should be in the review file.
    review_path = wiki_repo_path / "instructors" / "_aliases.review.md"
    assert review_path.exists()
    review_text = review_path.read_text()
    assert "kate" in review_text and "kate-benson" in review_text

    # Result summary should reflect what was written.
    assert result.safe_merge_groups >= 1
    assert result.ambiguous_groups >= 1
    assert result.instructor_pages_scanned == 6


def test_bootstrap_idempotent(wiki_repo_path: Path) -> None:
    _write_instructor(wiki_repo_path, "benji-schwimmer", sources_count=8)
    _write_instructor(wiki_repo_path, "benji-schremmer", sources_count=1)

    first = bootstrap_aliases(wiki_repo_path)
    aliases_first = (wiki_repo_path / "instructors" / "_aliases.yaml").read_text()

    second = bootstrap_aliases(wiki_repo_path)
    aliases_second = (wiki_repo_path / "instructors" / "_aliases.yaml").read_text()

    assert first.safe_merge_groups == second.safe_merge_groups
    assert aliases_first == aliases_second
