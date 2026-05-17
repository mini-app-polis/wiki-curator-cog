"""Tests for the deterministic derived-page fan-out."""

from __future__ import annotations

from pathlib import Path

import yaml

from wiki_curator_cog import markdown_utils as md
from wiki_curator_cog.derived_pages import (
    Contribution,
    apply_contributions,
    derived_slugs_by_type,
    plan_contributions,
)

# ── plan_contributions ──────────────────────────────────────────────────


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    body = text[4:]
    end = body.index("\n---\n")
    return yaml.safe_load(body[:end])


def test_plan_key_concepts_produce_concept_contributions() -> None:
    notes_json = {
        "key_concepts": [
            {"concept": "Anchor step", "detail": "The closing pattern."},
            {"concept": "Settle", "detail": "Sink into the floor."},
        ],
    }
    contribs = plan_contributions(
        notes_json=notes_json,
        canonical_instructors=["kate"],
        source_slug="2025-09-15-anchor-step",
        source_bucket="kate",
    )
    assert len(contribs) == 2
    assert all(c.page_type == "concept" for c in contribs)
    assert {c.page_slug for c in contribs} == {"anchor-step", "settle"}
    assert all(c.teacher == "kate" for c in contribs)
    # Citation appended automatically.
    assert "(([[sources/kate/2025-09-15-anchor-step]]))" not in contribs[0].paragraph_md
    assert "[[sources/kate/2025-09-15-anchor-step]]" in contribs[0].paragraph_md


def test_plan_multi_instructor_fans_out_to_all() -> None:
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [{"concept": "Frame", "detail": "Shoulders down."}]
        },
        canonical_instructors=["kate", "robert"],
        source_slug="2025-09-15-workshop",
        source_bucket="kate",
    )
    assert len(contribs) == 2
    assert {c.teacher for c in contribs} == {"kate", "robert"}


def test_plan_patterns_produce_technique_contributions() -> None:
    contribs = plan_contributions(
        notes_json={
            "patterns_and_sequences": [
                {"name": "Sugar Push", "description": "6-count basic."},
                {"name": "Whip"},
            ],
        },
        canonical_instructors=["kaiano"],
        source_slug="2025-09-15-level-2",
        source_bucket="kaiano",
    )
    technique = [c for c in contribs if c.page_type == "technique"]
    assert {c.page_slug for c in technique} == {"sugar-push", "whip"}


def test_plan_vocabulary_produces_concept_contributions() -> None:
    contribs = plan_contributions(
        notes_json={
            "vocabulary_terms": [
                {
                    "term": "compression",
                    "definition": "Energy stored when partners approach.",
                },
            ],
        },
        canonical_instructors=["robert"],
        source_slug="2025-06-28-vocab",
        source_bucket="robert",
    )
    assert len(contribs) == 1
    c = contribs[0]
    assert c.page_type == "concept"
    assert c.page_slug == "compression"
    assert "**compression**" in c.paragraph_md


def test_plan_references_produce_instructor_referenced_by() -> None:
    contribs = plan_contributions(
        notes_json={
            "references": [
                {"name": "Robert Royston", "type": "instructor", "context": "Cited."},
                {"name": "PJ", "context": "no type"},
                {"context": "no name — dropped"},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-foo",
        source_bucket="kate",
    )
    ref = [c for c in contribs if c.page_type == "instructor"]
    assert {c.page_slug for c in ref} == {"robert-royston", "pj"}
    assert all(c.kind == "referenced-by" for c in ref)


def test_plan_skips_unstructured_fields() -> None:
    contribs = plan_contributions(
        notes_json={
            "student_observations": [{"observation": "improving"}],
            "action_items": [{"action": "drill"}],
            "off_topic_notes": ["chatter"],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    assert contribs == []


# ── derived_slugs_by_type ───────────────────────────────────────────────


def test_derived_slugs_by_type_groups_and_sorts() -> None:
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="kate",
            paragraph_md="x",
            source_slug="s",
            source_bucket="kate",
        ),
        Contribution(
            page_type="technique",
            page_slug="sugar-push",
            teacher="kate",
            paragraph_md="x",
            source_slug="s",
            source_bucket="kate",
        ),
        Contribution(
            page_type="concept",
            page_slug="settle",
            teacher="kate",
            paragraph_md="x",
            source_slug="s",
            source_bucket="kate",
        ),
        # Duplicate (same page from two instructors) collapses to one.
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="robert",
            paragraph_md="x",
            source_slug="s",
            source_bucket="kate",
        ),
    ]
    out = derived_slugs_by_type(contribs)
    assert out == {
        "concepts": ["anchor-step", "settle"],
        "techniques": ["sugar-push"],
        "instructors": [],
        "terminology": [],
    }


# ── apply_contributions: first contribution creates page ────────────────


def test_apply_first_contribution_creates_concept_page(wiki_repo_path: Path) -> None:
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="kate",
            paragraph_md="Anchor as a sink, not a stay. ([[sources/kate/2025-09-15-foo]])",
            source_slug="2025-09-15-foo",
            source_bucket="kate",
        )
    ]
    touched = apply_contributions(contribs, wiki_repo_path=wiki_repo_path)
    assert len(touched) == 1
    page_path = wiki_repo_path / "concepts" / "anchor-step.md"
    assert page_path.exists()
    assert touched[0] == page_path

    fm = _frontmatter(page_path)
    assert fm["type"] == "concept"
    assert fm["slug"] == "anchor-step"
    assert fm["sources"] == ["2025-09-15-foo"]
    assert fm["teachers"] == ["kate"]
    assert fm["status"] == "stub"

    text = page_path.read_text()
    assert "## By teacher" in text
    assert "### Kate" in text
    assert "Anchor as a sink" in text


# ── apply: same source re-applied replaces in place (idempotent) ────────


def test_apply_same_source_replaces_paragraph_in_place(wiki_repo_path: Path) -> None:
    first = Contribution(
        page_type="concept",
        page_slug="anchor-step",
        teacher="kate",
        paragraph_md="First framing. ([[sources/kate/2025-09-15-foo]])",
        source_slug="2025-09-15-foo",
        source_bucket="kate",
    )
    apply_contributions([first], wiki_repo_path=wiki_repo_path)

    second = Contribution(
        page_type="concept",
        page_slug="anchor-step",
        teacher="kate",
        paragraph_md="Updated framing. ([[sources/kate/2025-09-15-foo]])",
        source_slug="2025-09-15-foo",
        source_bucket="kate",
    )
    apply_contributions([second], wiki_repo_path=wiki_repo_path)

    text = (wiki_repo_path / "concepts" / "anchor-step.md").read_text()
    assert "First framing." not in text
    assert "Updated framing." in text
    # sources frontmatter list should not duplicate.
    fm = _frontmatter(wiki_repo_path / "concepts" / "anchor-step.md")
    assert fm["sources"] == ["2025-09-15-foo"]


# ── apply: different source from same teacher appends a 2nd paragraph ──


def test_apply_different_source_same_teacher_appends(wiki_repo_path: Path) -> None:
    apply_contributions(
        [
            Contribution(
                page_type="concept",
                page_slug="anchor-step",
                teacher="kate",
                paragraph_md="First framing. ([[sources/kate/2025-09-15-foo]])",
                source_slug="2025-09-15-foo",
                source_bucket="kate",
            )
        ],
        wiki_repo_path=wiki_repo_path,
    )
    apply_contributions(
        [
            Contribution(
                page_type="concept",
                page_slug="anchor-step",
                teacher="kate",
                paragraph_md="Later framing. ([[sources/kate/2025-10-22-bar]])",
                source_slug="2025-10-22-bar",
                source_bucket="kate",
            )
        ],
        wiki_repo_path=wiki_repo_path,
    )
    text = (wiki_repo_path / "concepts" / "anchor-step.md").read_text()
    assert "First framing." in text
    assert "Later framing." in text
    # Both citations present, both sources in frontmatter.
    fm = _frontmatter(wiki_repo_path / "concepts" / "anchor-step.md")
    assert set(fm["sources"]) == {"2025-09-15-foo", "2025-10-22-bar"}


# ── apply: different teacher adds a new ### subsection ──────────────────


def test_apply_different_teacher_adds_subsection(wiki_repo_path: Path) -> None:
    apply_contributions(
        [
            Contribution(
                page_type="concept",
                page_slug="anchor-step",
                teacher="kate",
                paragraph_md="Kate framing. ([[sources/kate/2025-09-15-foo]])",
                source_slug="2025-09-15-foo",
                source_bucket="kate",
            )
        ],
        wiki_repo_path=wiki_repo_path,
    )
    apply_contributions(
        [
            Contribution(
                page_type="concept",
                page_slug="anchor-step",
                teacher="robert",
                paragraph_md="Robert framing. ([[sources/robert/2025-06-28-bar]])",
                source_slug="2025-06-28-bar",
                source_bucket="robert",
            )
        ],
        wiki_repo_path=wiki_repo_path,
    )
    text = (wiki_repo_path / "concepts" / "anchor-step.md").read_text()
    assert "### Kate" in text
    assert "### Robert" in text
    fm = _frontmatter(wiki_repo_path / "concepts" / "anchor-step.md")
    assert set(fm["teachers"]) == {"kate", "robert"}


# ── apply: referenced-by on instructor pages ────────────────────────────


def test_apply_referenced_by_creates_instructor_stub(wiki_repo_path: Path) -> None:
    apply_contributions(
        [
            Contribution(
                page_type="instructor",
                page_slug="robert-royston",
                teacher=None,
                paragraph_md="**Robert Royston** _(instructor)_ — Cited. ([[sources/kate/2025-09-15-foo]])",
                source_slug="2025-09-15-foo",
                source_bucket="kate",
                kind="referenced-by",
            )
        ],
        wiki_repo_path=wiki_repo_path,
    )
    page = wiki_repo_path / "instructors" / "robert-royston.md"
    assert page.exists()
    fm = _frontmatter(page)
    assert fm["type"] == "instructor"
    assert fm["status"] == "stub"
    assert fm["references_count"] == 1
    text = page.read_text()
    assert "## Referenced by" in text
    assert "Robert Royston" in text


# ── markdown_utils sanity ───────────────────────────────────────────────


def test_markdown_utils_round_trip_preserves_structure() -> None:
    text = (
        "---\ntype: concept\nslug: x\n---\n\n"
        "## Overview\n\nSome overview.\n\n"
        "## By teacher\n\n### Kate\n\nKate framing.\n\n### Robert\n\nRobert framing.\n"
    )
    page = md.parse(text)
    assert page.frontmatter["type"] == "concept"
    assert page.get_section("Overview") is not None
    by_teacher = page.get_section("By teacher")
    assert by_teacher is not None
    subs = md.split_h3(by_teacher)
    titles = [s[0] for s in subs if s[0] is not None]
    assert titles == ["Kate", "Robert"]
    # Round-trip preserves the structure (modulo whitespace).
    re_serialized = md.serialize(page)
    re_parsed = md.parse(re_serialized)
    assert re_parsed.frontmatter == page.frontmatter
    assert [s[0] for s in re_parsed.sections] == [s[0] for s in page.sections]
