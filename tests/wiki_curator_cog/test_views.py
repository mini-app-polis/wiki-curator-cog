"""Tests for views.regenerate_views — the four required filtered slices."""

from __future__ import annotations

from pathlib import Path

from wiki_curator_cog.views import REQUIRED_VIEWS, regenerate_views

# ── helpers ────────────────────────────────────────────────────────────


def _write_source(
    wiki_repo_path: Path,
    *,
    bucket: str,
    slug: str,
    session_date: str,
    session_type: str,
    instructors: list[str],
    students: list[str] | None = None,
    title: str | None = None,
    concepts: list[str] | None = None,
    techniques: list[str] | None = None,
) -> None:
    """Write a minimal source page to the tmp wiki for view scanning."""
    fm_lines = [
        "---",
        "type: source",
        f"session_date: {session_date}",
        f"session_type: {session_type}",
        f"instructors: [{', '.join(instructors)}]",
        f"students: [{', '.join(students or [])}]",
    ]
    if title:
        fm_lines.append(f"title: {title}")
    if concepts or techniques:
        fm_lines.append("contributed_to:")
        fm_lines.append(f"  concepts: [{', '.join(concepts or [])}]")
        fm_lines.append(f"  techniques: [{', '.join(techniques or [])}]")
    fm_lines += ["---", "", "## Summary", "", "(placeholder)", ""]

    bucket_dir = wiki_repo_path / "sources" / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / f"{slug}.md").write_text("\n".join(fm_lines))


# ── REQUIRED_VIEWS shape ───────────────────────────────────────────────


def test_required_views_match_spec() -> None:
    slugs = {spec.slug for spec in REQUIRED_VIEWS}
    assert slugs == {
        "kaiano-teaching-kate",
        "kaianos-canon",
        "roberts-canon",
        "full-model",
    }


# ── regenerate_views writes all four files ─────────────────────────────


def test_regenerate_writes_all_four_views_on_empty_wiki(wiki_repo_path: Path) -> None:
    written = regenerate_views(wiki_repo_path, curator_version="1.0.0")
    assert len(written) == 4
    for path in written:
        assert path.exists()
        # Empty-wiki views still write a marker body, not silence.
        text = path.read_text()
        assert "type: view" in text
        assert "_No sources currently match this view._" in text


# ── filter matching ────────────────────────────────────────────────────


def test_kaiano_teaching_kate_only_matches_both(wiki_repo_path: Path) -> None:
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-09-15-kaiano-with-kate",
        session_date="2025-09-15",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["kate"],
        title="kaiano coaches kate",
    )
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-10-01-kaiano-with-other",
        session_date="2025-10-01",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["sarah"],
        title="kaiano with sarah",
    )
    _write_source(
        wiki_repo_path,
        bucket="kate",
        slug="2025-11-01-kate-with-kaiano",
        session_date="2025-11-01",
        session_type="private_lesson",
        instructors=[
            "kate"
        ],  # Kaiano is the student, not instructor — should NOT match
        students=["kaiano"],
        title="kate teaches kaiano",
    )

    regenerate_views(wiki_repo_path, curator_version="1.0.0")

    ktk = (wiki_repo_path / "views" / "kaiano-teaching-kate.md").read_text()
    # Only the first source qualifies.
    assert "kaiano coaches kate" in ktk
    assert "kaiano with sarah" not in ktk
    assert "kate teaches kaiano" not in ktk


def test_kaianos_canon_includes_all_kaiano_taught(wiki_repo_path: Path) -> None:
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-09-15-a",
        session_date="2025-09-15",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["kate"],
        title="A",
    )
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-10-01-b",
        session_date="2025-10-01",
        session_type="group_class",
        instructors=["kaiano"],
        title="B",
    )
    # Not Kaiano — should not appear.
    _write_source(
        wiki_repo_path,
        bucket="robert",
        slug="2025-06-28-c",
        session_date="2025-06-28",
        session_type="private_lesson",
        instructors=["robert"],
        students=["kaiano"],
        title="C",
    )

    regenerate_views(wiki_repo_path, curator_version="1.0.0")

    canon = (wiki_repo_path / "views" / "kaianos-canon.md").read_text()
    assert "A" in canon and "B" in canon
    assert "C" not in canon


def test_full_model_includes_every_source_chronologically(wiki_repo_path: Path) -> None:
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-11-01-late",
        session_date="2025-11-01",
        session_type="private_lesson",
        instructors=["kaiano"],
        title="late",
    )
    _write_source(
        wiki_repo_path,
        bucket="robert",
        slug="2025-06-28-early",
        session_date="2025-06-28",
        session_type="private_lesson",
        instructors=["robert"],
        title="early",
    )

    regenerate_views(wiki_repo_path, curator_version="1.0.0")

    text = (wiki_repo_path / "views" / "full-model.md").read_text()
    early_idx = text.index("early")
    late_idx = text.index("late")
    assert early_idx < late_idx, "sources should be in chronological order"


def test_view_concepts_aggregated_from_contributed_to(wiki_repo_path: Path) -> None:
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-09-15-x",
        session_date="2025-09-15",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["kate"],
        title="x",
        concepts=["anchor", "settle"],
        techniques=["sugar-push"],
    )
    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-10-01-y",
        session_date="2025-10-01",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["kate"],
        title="y",
        concepts=["anchor", "frame"],  # 'anchor' deduped across sources
        techniques=["whip"],
    )

    regenerate_views(wiki_repo_path, curator_version="1.0.0")
    ktk = (wiki_repo_path / "views" / "kaiano-teaching-kate.md").read_text()
    assert "[[concepts/anchor]]" in ktk
    assert "[[concepts/settle]]" in ktk
    assert "[[concepts/frame]]" in ktk
    assert "[[techniques/sugar-push]]" in ktk
    assert "[[techniques/whip]]" in ktk


# ── idempotency ────────────────────────────────────────────────────────


def test_regenerate_is_idempotent_same_date(wiki_repo_path: Path) -> None:
    import datetime as dt

    _write_source(
        wiki_repo_path,
        bucket="kaiano",
        slug="2025-09-15-x",
        session_date="2025-09-15",
        session_type="private_lesson",
        instructors=["kaiano"],
        students=["kate"],
    )
    fixed = dt.date(2026, 1, 1)
    first = regenerate_views(
        wiki_repo_path, curator_version="1.0.0", regenerated_at=fixed
    )
    first_texts = {p.name: p.read_text() for p in first}
    regenerate_views(wiki_repo_path, curator_version="1.0.0", regenerated_at=fixed)
    second_texts = {
        p.name: p.read_text() for p in (wiki_repo_path / "views").glob("*.md")
    }
    assert first_texts == second_texts
