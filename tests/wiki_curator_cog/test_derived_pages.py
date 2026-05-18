"""Tests for the deterministic derived-page fan-out."""

from __future__ import annotations

from pathlib import Path

import yaml

from wiki_curator_cog import markdown_utils as md
from wiki_curator_cog.aliases import AliasMap
from wiki_curator_cog.derived_pages import (
    Contribution,
    apply_contributions,
    derived_slugs_by_type,
    plan_contributions,
    wipe_derived_pages,
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
    concept_contribs = [c for c in contribs if c.page_type == "concept"]
    assert len(concept_contribs) == 2
    assert {c.page_slug for c in concept_contribs} == {"anchor-step", "settle"}
    assert all(c.teacher == "kate" for c in concept_contribs)
    # Citation appended automatically.
    assert (
        "(([[sources/kate/2025-09-15-anchor-step]]))"
        not in concept_contribs[0].paragraph_md
    )
    assert "[[sources/kate/2025-09-15-anchor-step]]" in concept_contribs[0].paragraph_md


def test_plan_multi_instructor_fans_out_to_all() -> None:
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [{"concept": "Frame", "detail": "Shoulders down."}]
        },
        canonical_instructors=["kate", "robert"],
        source_slug="2025-09-15-workshop",
        source_bucket="kate",
    )
    by_teacher = [c for c in contribs if c.kind == "by-teacher"]
    assert len(by_teacher) == 2
    assert {c.teacher for c in by_teacher} == {"kate", "robert"}


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
    concept_contribs = [c for c in contribs if c.page_type == "concept"]
    assert len(concept_contribs) == 1
    c = concept_contribs[0]
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
    ref = [
        c for c in contribs if c.page_type == "instructor" and c.kind == "referenced-by"
    ]
    assert {c.page_slug for c in ref} == {"robert-royston", "pj"}


def test_plan_drops_sentence_shaped_concepts() -> None:
    """Upstream LLM occasionally puts a full sentence in concept.name —
    those should not become their own concept pages."""
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                {"concept": "Anchor step", "detail": "Sink, don't bounce."},
                # 14 words, clearly a sentence pretending to be a concept.
                {
                    "concept": (
                        "leaders should give space on the anchor so followers "
                        "can continue traveling backward"
                    ),
                    "detail": "...",
                },
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {c.page_slug for c in contribs}
    assert "anchor-step" in slugs
    assert not any("leaders-should" in s for s in slugs)


def test_plan_drops_conjunction_shaped_concepts() -> None:
    """Concepts joined by vs/or are multi-concept smushes, not noun-phrases."""
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                # Real noun-phrase, kept.
                {"concept": "Settle", "detail": "Sink into the floor."},
                # "X vs Y" — rejected.
                {
                    "concept": "eccentric vs concentric muscle engagement",
                    "detail": "...",
                },
                # "X or Y" — rejected.
                {"concept": "connect at or below the connection", "detail": "..."},
                # hyphenated "vs" — rejected via hyphen tokenization.
                {"concept": "eccentric-vs-concentric", "detail": "..."},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {c.page_slug for c in contribs if c.page_type == "concept"}
    assert slugs == {"settle"}


def test_plan_drops_repeated_word_concepts() -> None:
    """Repeated word in a 'concept' almost always means a smushed list."""
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                {"concept": "Frame", "detail": "..."},
                {"concept": "competitive ceiling competitive floor", "detail": "..."},
                {"concept": "walk walk triple step triple step", "detail": "..."},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {c.page_slug for c in contribs if c.page_type == "concept"}
    assert slugs == {"frame"}


def test_plan_drops_variation_suffix_concepts() -> None:
    """`X variation` belongs on the technique page, not as its own concept."""
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                {"concept": "Anchor", "detail": "..."},
                {"concept": "parallel hips sugar tuck variation", "detail": "..."},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {c.page_slug for c in contribs if c.page_type == "concept"}
    assert slugs == {"anchor"}


def test_plan_drops_interior_stopword_concepts() -> None:
    """`pulling the trigger on redirection` is sentence-shaped (interior 'the')."""
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                {"concept": "Stretch", "detail": "..."},
                {"concept": "pulling the trigger on redirection", "detail": "..."},
                # Interior 'of' — rejected.
                {"concept": "release of compression sequence", "detail": "..."},
                # First-position 'the' is allowed (not interior).
                {"concept": "the anchor", "detail": "..."},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {c.page_slug for c in contribs}
    assert "stretch" in slugs
    assert "the-anchor" in slugs
    assert not any("pulling" in s for s in slugs)
    assert not any("release-of" in s for s in slugs)


def test_plan_drops_non_person_references() -> None:
    """References that are events/schools/objects shouldn't create
    instructor pages. Only individual-person references should."""
    contribs = plan_contributions(
        notes_json={
            "references": [
                # Allowed: clear person, ≤3 words, title-case.
                {"name": "Robert Royston", "type": "instructor"},
                {"name": "PJ", "context": "no type"},
                # Allowed via type even though name shape is iffy.
                {"name": "John M", "type": "dancer"},
                # Blocked: multi-person.
                {"name": "Ben and Cameo", "type": "instructor"},
                {"name": "KP & Bryn"},
                # Blocked: hedge phrases.
                {"name": "alyssa (last name not stated)"},
                {"name": "Benji Schwimmer (implied)"},
                # Blocked: looks like event/org (no type, too many words).
                {"name": "Austin Swing Dance Championships"},
                {"name": "American Journal of Science"},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    slugs = {
        c.page_slug
        for c in contribs
        if c.page_type == "instructor" and c.kind == "referenced-by"
    }
    assert slugs == {"robert-royston", "pj", "john-m"}


def test_plan_skips_unstructured_fields() -> None:
    """student_observations / action_items / off_topic_notes never produce
    concept, technique, or referenced-by instructor contributions.

    The only contribution that DOES come out is the
    instructor-as-author entry for the canonical instructor — every
    source belongs on its instructor's ## Sources list regardless of
    whether the body had ingestable content.
    """
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
    # No concept / technique / referenced-by contributions.
    assert not [c for c in contribs if c.page_type in {"concept", "technique"}]
    assert not [c for c in contribs if c.kind == "referenced-by"]
    # Exactly one as-author contribution for ``kate``.
    as_author = [c for c in contribs if c.kind == "as-author"]
    assert len(as_author) == 1
    assert as_author[0].page_slug == "kate"


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


# ── vocab canonicalization ──────────────────────────────────────────────


def _make_alias_map(wiki_repo_path: Path, *, relative_dir: str, body: str) -> AliasMap:
    """Helper: write a vocab _aliases.yaml file and return the loaded map."""
    target_dir = wiki_repo_path / relative_dir
    target_dir.mkdir(exist_ok=True)
    (target_dir / "_aliases.yaml").write_text(body)
    return AliasMap.load(wiki_repo_path, relative_dir=relative_dir)


def test_plan_concept_canonicalizes_via_plural_collapse(
    wiki_repo_path: Path,
) -> None:
    concept_aliases = _make_alias_map(wiki_repo_path, relative_dir="concepts", body="")
    technique_aliases = _make_alias_map(
        wiki_repo_path, relative_dir="techniques", body=""
    )

    notes_json = {
        "key_concepts": [
            {"concept": "Anchor step", "detail": "Singular form."},
            {"concept": "Anchor steps", "detail": "Plural form."},
        ],
    }
    contribs = plan_contributions(
        notes_json=notes_json,
        canonical_instructors=["kate"],
        source_slug="2025-09-15-anchor",
        source_bucket="kate",
        concept_aliases=concept_aliases,
        technique_aliases=technique_aliases,
    )

    concept_contribs = [c for c in contribs if c.page_type == "concept"]
    # Both contributions land on the same canonical slug.
    assert {c.page_slug for c in concept_contribs} == {"anchor-step"}
    # The plural form is recorded as ``raw_slug`` so apply_contributions
    # can surface it on the canonical page's ``aliases:`` frontmatter.
    raw_slugs = [c.raw_slug for c in concept_contribs]
    assert "anchor-steps" in raw_slugs
    # The singular form contributed without needing a raw_slug.
    assert None in raw_slugs


def test_plan_concept_canonicalizes_via_alias_map(wiki_repo_path: Path) -> None:
    concept_aliases = _make_alias_map(
        wiki_repo_path,
        relative_dir="concepts",
        body="anchor: anchor-step\nanchoring-action: anchor-step\n",
    )
    technique_aliases = _make_alias_map(
        wiki_repo_path, relative_dir="techniques", body=""
    )

    notes_json = {
        "vocabulary_terms": [
            {"term": "Anchor", "definition": "The grounding action."},
            {"term": "Anchoring action", "definition": "Same idea, different name."},
        ],
    }
    contribs = plan_contributions(
        notes_json=notes_json,
        canonical_instructors=["kate"],
        source_slug="2025-09-15-anchor",
        source_bucket="kate",
        concept_aliases=concept_aliases,
        technique_aliases=technique_aliases,
    )

    concept_contribs = [c for c in contribs if c.page_type == "concept"]
    assert {c.page_slug for c in concept_contribs} == {"anchor-step"}
    raw_slugs = {c.raw_slug for c in concept_contribs}
    assert raw_slugs == {"anchor", "anchoring-action"}


def test_plan_technique_canonicalizes_via_alias_map(wiki_repo_path: Path) -> None:
    concept_aliases = _make_alias_map(wiki_repo_path, relative_dir="concepts", body="")
    technique_aliases = _make_alias_map(
        wiki_repo_path,
        relative_dir="techniques",
        body="basic-whip: whip\nwhip-basic: whip\n",
    )

    notes_json = {
        "patterns_and_sequences": [
            {"name": "Basic whip", "description": "Original framing."},
            {"name": "Whip basic", "description": "Same thing, reordered."},
            {"name": "Whip", "description": "Just whip."},
        ],
    }
    contribs = plan_contributions(
        notes_json=notes_json,
        canonical_instructors=["kate"],
        source_slug="2025-09-15-whip",
        source_bucket="kate",
        concept_aliases=concept_aliases,
        technique_aliases=technique_aliases,
    )

    technique_contribs = [c for c in contribs if c.page_type == "technique"]
    assert {c.page_slug for c in technique_contribs} == {"whip"}
    raw_slugs = {c.raw_slug for c in technique_contribs}
    # ``whip`` mapped to itself contributes None; the other two record
    # their distinct raw forms.
    assert raw_slugs == {"basic-whip", "whip-basic", None}


def test_plan_without_alias_maps_falls_back_to_legacy_slugify() -> None:
    # Tests / legacy callers that don't pass alias maps preserve the
    # pre-vocab-collapse behavior: no plural merge, no canonicalization.
    notes_json = {
        "key_concepts": [
            {"concept": "Anchor step", "detail": "x"},
            {"concept": "Anchor steps", "detail": "y"},
        ],
    }
    contribs = plan_contributions(
        notes_json=notes_json,
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    concept_contribs = [c for c in contribs if c.page_type == "concept"]
    # Without alias maps, the two forms produce two distinct slugs.
    assert {c.page_slug for c in concept_contribs} == {"anchor-step", "anchor-steps"}
    # And raw_slug stays None on every concept contribution.
    assert all(c.raw_slug is None for c in concept_contribs)


def test_apply_contributions_records_alias_on_canonical_page(
    wiki_repo_path: Path,
) -> None:
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="kate",
            paragraph_md="An anchor framing. ([[sources/kate/s1]])",
            source_slug="s1",
            source_bucket="kate",
            raw_slug="anchor-steps",
        ),
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="kate",
            paragraph_md="Another framing. ([[sources/kate/s2]])",
            source_slug="s2",
            source_bucket="kate",
            raw_slug="anchor",  # collapsed via alias map
        ),
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    page_path = wiki_repo_path / "concepts" / "anchor-step.md"
    fm = _frontmatter(page_path)
    assert set(fm.get("aliases", [])) == {"anchor-steps", "anchor"}
    # Sources still accumulate normally.
    assert set(fm.get("sources", [])) == {"s1", "s2"}


def test_apply_contributions_skips_alias_when_raw_matches_canonical(
    wiki_repo_path: Path,
) -> None:
    # When the raw slug already equals the canonical, no alias entry —
    # the page is the canonical FOR that exact term, no variant to record.
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="anchor-step",
            teacher="kate",
            paragraph_md="Framing. ([[sources/kate/s1]])",
            source_slug="s1",
            source_bucket="kate",
            raw_slug=None,
        ),
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    fm = _frontmatter(wiki_repo_path / "concepts" / "anchor-step.md")
    assert fm.get("aliases", []) == []


# ── wipe_derived_pages ──────────────────────────────────────────────────


def test_wipe_derived_pages_deletes_md_files_but_preserves_alias_maps(
    wiki_repo_path: Path,
) -> None:
    # Seed each derived directory with a page + an alias map.
    for sub in ("concepts", "techniques", "instructors", "terminology"):
        (wiki_repo_path / sub).mkdir(exist_ok=True)
        (wiki_repo_path / sub / "example.md").write_text("# example\n")
        (wiki_repo_path / sub / "_aliases.yaml").write_text("# alias map\n")

    removed = wipe_derived_pages(wiki_repo_path)
    assert len(removed) == 4
    assert all(p.suffix == ".md" for p in removed)

    for sub in ("concepts", "techniques", "instructors", "terminology"):
        assert not (wiki_repo_path / sub / "example.md").exists()
        # Alias maps survive.
        assert (wiki_repo_path / sub / "_aliases.yaml").exists()


def test_wipe_derived_pages_is_safe_on_empty_directories(
    wiki_repo_path: Path,
) -> None:
    # No derived pages anywhere — wipe should just return [].
    removed = wipe_derived_pages(wiki_repo_path)
    assert removed == []


def test_wipe_derived_pages_does_not_touch_sources_or_index(
    wiki_repo_path: Path,
) -> None:
    # Source pages and root-level files must be left alone.
    (wiki_repo_path / "sources" / "kaiano" / "example.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (wiki_repo_path / "sources" / "kaiano" / "example.md").write_text("source")
    (wiki_repo_path / "index.md").write_text("# index")
    (wiki_repo_path / "log.md").write_text("# log")

    # Plus one derived page so the wipe has something to do.
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "x.md").write_text("derived")

    removed = wipe_derived_pages(wiki_repo_path)
    assert len(removed) == 1
    assert (wiki_repo_path / "sources" / "kaiano" / "example.md").exists()
    assert (wiki_repo_path / "index.md").exists()
    assert (wiki_repo_path / "log.md").exists()


# ── as-author instructor contributions ──────────────────────────────────


def test_plan_emits_as_author_for_each_canonical_instructor() -> None:
    """Every source produces one as-author contribution per instructor.

    Workshop with two instructors → two contributions targeting their
    instructor pages, both kind=as-author.
    """
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [{"concept": "Frame", "detail": "Shoulders down."}],
        },
        canonical_instructors=["kate", "robert"],
        source_slug="2025-09-15-workshop",
        source_bucket="kate",
    )
    as_author = [c for c in contribs if c.kind == "as-author"]
    assert len(as_author) == 2
    assert {c.page_slug for c in as_author} == {"kate", "robert"}
    assert all(c.page_type == "instructor" for c in as_author)


def test_plan_as_author_carries_concepts_techniques_taught() -> None:
    """The as-author contribution carries the per-instructor slug list.

    Kate teaches Settle on this source, Robert teaches Frame. Each
    instructor's as-author contribution lists only the slugs they're
    attributed to.
    """
    contribs = plan_contributions(
        notes_json={
            "key_concepts": [
                {"concept": "Settle", "detail": "Sink."},
            ],
            "patterns_and_sequences": [
                {"name": "Whip", "description": "Drive forward."},
            ],
        },
        canonical_instructors=["kate"],
        source_slug="2025-09-15-x",
        source_bucket="kate",
    )
    as_author = [c for c in contribs if c.kind == "as-author"]
    assert len(as_author) == 1
    kate = as_author[0]
    assert kate.page_slug == "kate"
    assert kate.concepts_taught == ["settle"]
    assert kate.techniques_taught == ["whip"]


def test_plan_as_author_bullet_includes_source_metadata() -> None:
    """The bullet text shows date · title · session-type."""
    import datetime as _dt

    contribs = plan_contributions(
        notes_json={},
        canonical_instructors=["kate"],
        source_slug="2025-09-15-anchors",
        source_bucket="kate",
        session_date=_dt.date(2025, 9, 15),
        session_type="private_lesson",
        title="Anchors and stretch",
    )
    as_author = [c for c in contribs if c.kind == "as-author"]
    assert len(as_author) == 1
    bullet = as_author[0].paragraph_md
    assert bullet.startswith("- ")
    assert "**2025-09-15**" in bullet
    assert "[[sources/kate/2025-09-15-anchors|Anchors and stretch]]" in bullet
    assert "private lesson" in bullet


def test_apply_as_author_populates_sources_count_and_taught_lists(
    wiki_repo_path: Path,
) -> None:
    """An as-author contribution lands as a ## Sources bullet plus
    sources_count, concepts_taught, techniques_taught frontmatter."""
    contribs = [
        Contribution(
            page_type="instructor",
            page_slug="kate",
            teacher=None,
            paragraph_md=(
                "- **2025-09-15** — "
                "[[sources/kate/2025-09-15-anchors|Anchors and stretch]] · "
                "private lesson"
            ),
            source_slug="2025-09-15-anchors",
            source_bucket="kate",
            kind="as-author",
            concepts_taught=["anchor-step", "settle"],
            techniques_taught=["whip"],
        ),
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    page_path = wiki_repo_path / "instructors" / "kate.md"
    assert page_path.exists()
    fm = _frontmatter(page_path)
    assert fm["sources"] == ["2025-09-15-anchors"]
    assert fm["sources_count"] == 1
    assert set(fm["concepts_taught"]) == {"anchor-step", "settle"}
    assert set(fm["techniques_taught"]) == {"whip"}
    body = page_path.read_text()
    assert "## Sources" in body
    assert "[[sources/kate/2025-09-15-anchors|Anchors and stretch]]" in body


def test_apply_as_author_is_idempotent(wiki_repo_path: Path) -> None:
    """Re-applying the same as-author contribution replaces the bullet
    rather than appending a duplicate, and sources_count stays at 1."""
    base_kwargs = dict(
        page_type="instructor",
        page_slug="kate",
        teacher=None,
        source_slug="2025-09-15-anchors",
        source_bucket="kate",
        kind="as-author",
        concepts_taught=["anchor-step"],
        techniques_taught=[],
    )
    first = Contribution(
        paragraph_md=(
            "- **2025-09-15** — [[sources/kate/2025-09-15-anchors|First title]]"
        ),
        **base_kwargs,
    )
    apply_contributions([first], wiki_repo_path=wiki_repo_path)

    # Re-ingest at a bumped title → bullet must be replaced, not duplicated.
    second = Contribution(
        paragraph_md=(
            "- **2025-09-15** — [[sources/kate/2025-09-15-anchors|Updated title]]"
        ),
        **base_kwargs,
    )
    apply_contributions([second], wiki_repo_path=wiki_repo_path)

    page_path = wiki_repo_path / "instructors" / "kate.md"
    body = page_path.read_text()
    # The slug appears twice in the file as a whole: once in the
    # frontmatter ``sources:`` list and once in the ## Sources bullet.
    # The bullet itself must not be duplicated.
    sources_section = body.split("## Sources", 1)[1]
    assert sources_section.count("2025-09-15-anchors") == 1
    assert "Updated title" in body
    assert "First title" not in body
    fm = _frontmatter(page_path)
    assert fm["sources_count"] == 1
    assert fm["sources"] == ["2025-09-15-anchors"]


def test_apply_as_author_and_referenced_by_use_separate_sections(
    wiki_repo_path: Path,
) -> None:
    """The same instructor can be both author of one source and
    referenced by another. ## Sources gets the as-author bullet;
    ## Referenced by gets the referenced-by bullet."""
    contribs = [
        Contribution(
            page_type="instructor",
            page_slug="robert",
            teacher=None,
            paragraph_md="- **2025-06-28** — [[sources/robert/2025-06-28-x|Workshop]]",
            source_slug="2025-06-28-x",
            source_bucket="robert",
            kind="as-author",
        ),
        Contribution(
            page_type="instructor",
            page_slug="robert",
            teacher=None,
            paragraph_md="**Robert** — Cited as influence ([[sources/kaiano/2025-10-13-x]])",
            source_slug="2025-10-13-x",
            source_bucket="kaiano",
            kind="referenced-by",
        ),
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    page_path = wiki_repo_path / "instructors" / "robert.md"
    body = page_path.read_text()
    fm = _frontmatter(page_path)

    # Frontmatter counts split cleanly.
    assert fm["sources_count"] == 1
    assert fm["references_count"] == 1
    # The two sections both have one bullet each.
    sources_section = body.split("## Sources", 1)[1].split("## Referenced by", 1)[0]
    referenced_section = body.split("## Referenced by", 1)[1]
    assert "2025-06-28-x" in sources_section
    assert "2025-10-13-x" in referenced_section
    # Neither bullet leaks across sections.
    assert "2025-10-13-x" not in sources_section
    assert "2025-06-28-x" not in referenced_section


# ── status promotion ────────────────────────────────────────────────────


def test_apply_promotes_concept_status_at_three_sources(
    wiki_repo_path: Path,
) -> None:
    """Three distinct sources crosses the synthesis threshold → status
    auto-promotes from stub to draft."""
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="settle",
            teacher="kate",
            paragraph_md=f"Framing #{i}. ([[sources/kate/s{i}]])",
            source_slug=f"s{i}",
            source_bucket="kate",
        )
        for i in range(1, 4)
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    fm = _frontmatter(wiki_repo_path / "concepts" / "settle.md")
    assert fm["status"] == "draft"


def test_apply_keeps_stub_below_threshold(wiki_repo_path: Path) -> None:
    """Two sources is below the promotion threshold → status stays stub."""
    contribs = [
        Contribution(
            page_type="concept",
            page_slug="settle",
            teacher="kate",
            paragraph_md=f"Framing #{i}. ([[sources/kate/s{i}]])",
            source_slug=f"s{i}",
            source_bucket="kate",
        )
        for i in range(1, 3)
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    fm = _frontmatter(wiki_repo_path / "concepts" / "settle.md")
    assert fm["status"] == "stub"


def test_apply_promotes_instructor_status_at_three_sources(
    wiki_repo_path: Path,
) -> None:
    """Three as-author sources promote the instructor page to draft."""
    contribs = [
        Contribution(
            page_type="instructor",
            page_slug="kate",
            teacher=None,
            paragraph_md=f"- [[sources/kate/s{i}|s{i}]]",
            source_slug=f"s{i}",
            source_bucket="kate",
            kind="as-author",
        )
        for i in range(1, 4)
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    fm = _frontmatter(wiki_repo_path / "instructors" / "kate.md")
    assert fm["status"] == "draft"


def test_apply_does_not_demote_human_curated_status(
    wiki_repo_path: Path,
) -> None:
    """If a human has promoted a page past stub already, synthesis
    must not push it back down."""
    # Pre-populate the page at status=mature.
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    existing = """---
type: concept
slug: settle
sources:
  - s1
  - s2
  - s3
teachers:
  - kate
status: mature
---

## Overview

Curated overview.

## By teacher
"""
    (wiki_repo_path / "concepts" / "settle.md").write_text(existing)

    contribs = [
        Contribution(
            page_type="concept",
            page_slug="settle",
            teacher="kate",
            paragraph_md="New framing. ([[sources/kate/s4]])",
            source_slug="s4",
            source_bucket="kate",
        ),
    ]
    apply_contributions(contribs, wiki_repo_path=wiki_repo_path)

    fm = _frontmatter(wiki_repo_path / "concepts" / "settle.md")
    assert fm["status"] == "mature"


# ── empty-description placeholder ───────────────────────────────────────


def test_empty_detail_renders_placeholder_not_parenthetical(
    wiki_repo_path: Path,
) -> None:
    """When notes_json gives a name but no detail, the contribution
    paragraph reads as the placeholder rather than ``(Name).`` stub."""
    contribs = plan_contributions(
        notes_json={
            "patterns_and_sequences": [
                {"name": "Sugar Push"},  # no description
            ],
        },
        canonical_instructors=["kaiano"],
        source_slug="2025-09-15-x",
        source_bucket="kaiano",
    )
    technique = [c for c in contribs if c.page_type == "technique"]
    assert len(technique) == 1
    para = technique[0].paragraph_md
    assert "Referenced without elaboration" in para
    # The bad old behavior emitted "(Sugar Push)." as the body.
    assert "(Sugar Push)" not in para
    # Citation still present so the source-count synthesis still picks it up.
    assert "[[sources/kaiano/2025-09-15-x]]" in para


# ── loose acronym filter ────────────────────────────────────────────────


def test_passes_instructor_filter_rejects_loose_acronyms() -> None:
    """Title-case 4-letter org acronyms (LLM case-folded ``Asdc``,
    ``Wsdc``, all-consonant strings) should not pass the filter even
    when the strict isupper() check fails."""
    from wiki_curator_cog.derived_pages import _passes_instructor_filter

    # Loose-acronym cases — must be rejected.
    assert not _passes_instructor_filter("Asdc", "")
    assert not _passes_instructor_filter("Wsdc", "")
    assert not _passes_instructor_filter("Dcsx", "")  # zero vowels

    # Real 4-letter names — must still pass.
    assert _passes_instructor_filter("Kate", "")
    assert _passes_instructor_filter("Joel", "")
    assert _passes_instructor_filter("Ardy", "")  # y-as-vowel
    assert _passes_instructor_filter("Jess", "")  # 1 mid-word vowel + repeat
    assert _passes_instructor_filter("Hugh", "")  # 1 mid-word vowel + repeat


def test_wipe_derived_pages_preserves_non_md_files(wiki_repo_path: Path) -> None:
    # A stray non-md file in a derived directory (e.g. an image, a README)
    # must NOT be deleted — only .md files are derived output.
    (wiki_repo_path / "concepts").mkdir(exist_ok=True)
    (wiki_repo_path / "concepts" / "diagram.png").write_bytes(b"\x89PNG\r\n")
    (wiki_repo_path / "concepts" / "x.md").write_text("derived")

    removed = wipe_derived_pages(wiki_repo_path)
    assert removed == [wiki_repo_path / "concepts" / "x.md"]
    assert (wiki_repo_path / "concepts" / "diagram.png").exists()


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
