"""Tests for render.py — stateless export → markdown bundle."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from wiki_curator_cog.models import (
    WcsAttribution,
    WcsDefinition,
    WcsDrillPurpose,
    WcsEntity,
    WcsInstructor,
    WcsReference,
    WcsRelation,
    WcsSource,
    WcsTechniqueRequirement,
    WcsWikiExport,
)
from wiki_curator_cog.render import (
    build_indexes,
    export_attributions_for_instructor,
    find_dangling_references,
    render_bundle,
    render_entity_page,
    slugify,
)


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


def _make_export() -> WcsWikiExport:
    kaiano = WcsInstructor(
        id=_id("instructor-kaiano"),
        slug="kaiano",
        canonical_name="Kaiano Levine",
        aliases=["Kaiano"],
    )
    kate = WcsInstructor(
        id=_id("instructor-kate"),
        slug="kate",
        canonical_name="Kate Smith",
    )
    amy = WcsInstructor(
        id=_id("instructor-amy"),
        slug="amy",
        canonical_name="Amy",
    )

    source_kaiano = WcsSource(
        id=_id("source-kaiano"),
        transcript_id=_id("transcript-kaiano"),
        title="Anchor Step",
        session_date=dt.date(2025, 3, 1),
        session_type="private_lesson",
        instructors_raw=["Kaiano Levine"],
        students_raw=["Kate Smith"],
        organization="Studio A",
        visibility="private",
        is_default_visible=True,
        created_at=dt.datetime(2025, 3, 1, 12, 0, tzinfo=dt.UTC),
    )
    source_robert = WcsSource(
        id=_id("source-robert"),
        transcript_id=_id("transcript-robert"),
        title=None,
        session_date=dt.date(2025, 4, 2),
        session_type="group_class",
        instructors_raw=["Robert Jones"],
        students_raw=[],
        organization="",
        visibility="public",
        is_default_visible=True,
        created_at=dt.datetime(2025, 4, 2, 12, 0, tzinfo=dt.UTC),
    )
    source_coauth = WcsSource(
        id=_id("source-coauth"),
        transcript_id=_id("transcript-coauth"),
        title="Kaiano + Amy lesson",
        session_date=dt.date(2026, 5, 27),
        session_type="group_class",
        instructors_raw=["kaiano", "amy"],
        students_raw=[],
        organization="Swingesota",
        visibility="public",
        is_default_visible=True,
        created_at=dt.datetime(2026, 5, 27, 19, 0, tzinfo=dt.UTC),
    )

    concept = WcsEntity(
        id=_id("entity-concept"),
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="concept",
        overview_md="Neutral overview of anchor step.",
        status="draft",
        aliases=["anchor"],
    )
    technique = WcsEntity(
        id=_id("entity-technique"),
        slug="whisk",
        canonical_name="Whisk",
        kind="technique",
        status="stub",
    )
    pattern = WcsEntity(
        id=_id("entity-pattern"),
        slug="sugar-push",
        canonical_name="Sugar Push",
        kind="pattern",
        status="draft",
    )
    drill = WcsEntity(
        id=_id("entity-drill"),
        slug="balance-drill",
        canonical_name="Balance Drill",
        kind="drill",
    )

    attributions = [
        WcsAttribution(
            id=_id("attr-kaiano-concept"),
            source_id=source_kaiano.id,
            entity_id=concept.id,
            instructor_id=kaiano.id,
            attribution_kind="taught",
            prose="Kaiano teaches anchor step with weight on the ball of the foot.",
            raw_term="anchor step",
            position=1,
        ),
        WcsAttribution(
            id=_id("attr-null-instructor"),
            source_id=source_robert.id,
            entity_id=concept.id,
            instructor_id=None,
            attribution_kind="taught",
            prose="Robert frames anchor step differently.",
            raw_term="anchor step",
            position=1,
        ),
        WcsAttribution(
            id=_id("attr-mistake"),
            source_id=source_kaiano.id,
            entity_id=concept.id,
            instructor_id=kaiano.id,
            attribution_kind="mistake",
            mistake_text="Rolling through the heel",
            correction_text="Stay on the ball of the foot",
            raw_term="anchor step",
            position=2,
        ),
        WcsAttribution(
            id=_id("attr-coauth"),
            source_id=source_coauth.id,
            entity_id=concept.id,
            instructor_id=None,
            attribution_kind="taught",
            prose="Co-taught content example.",
            raw_term="anchor step",
            position=0,
            origin="extraction",
        ),
    ]

    definitions = [
        WcsDefinition(
            id=_id("def-concept"),
            entity_id=concept.id,
            source_id=source_kaiano.id,
            instructor_id=kaiano.id,
            term="anchor step",
            definition="The settled position at end of a pattern.",
            position=1,
        )
    ]

    relations = [
        WcsRelation(
            id=_id("rel-concept-technique"),
            from_entity_id=concept.id,
            to_entity_id=technique.id,
            relation_kind="prerequisite-of",
            source_id=source_kaiano.id,
            prose="Anchor step precedes whisk entry.",
        )
    ]

    drill_purposes = [
        WcsDrillPurpose(
            id=_id("purpose-drill"),
            drill_entity_id=drill.id,
            source_id=source_kaiano.id,
            skill_name="Balance",
            skill_slug="balance",
            prose="Trains single-foot balance.",
            focus_context="Before social dancing.",
        )
    ]

    technique_requirements = [
        WcsTechniqueRequirement(
            id=_id("req-technique"),
            technique_entity_id=technique.id,
            source_id=source_kaiano.id,
            skill_name="Anchor step",
            skill_slug="anchor-step",
            prose="Requires a stable anchor.",
        )
    ]

    references = [
        WcsReference(
            id=_id("ref-source"),
            source_id=source_kaiano.id,
            referenced_name="Ben",
            context="Mentioned as a great social dancer.",
            ref_type="dancer",
            created_at=dt.datetime(2025, 3, 1, 12, 0, tzinfo=dt.UTC),
        )
    ]

    return WcsWikiExport(
        entities=[concept, technique, pattern, drill],
        instructors=[kaiano, kate, amy],
        sources=[source_kaiano, source_robert, source_coauth],
        attributions=attributions,
        definitions=definitions,
        relations=relations,
        drill_purposes=drill_purposes,
        technique_requirements=technique_requirements,
        references=references,
    )


@pytest.fixture
def export() -> WcsWikiExport:
    return _make_export()


@pytest.fixture
def rendered_at() -> dt.date:
    return dt.date(2025, 5, 28)


def test_entity_pages_land_in_correct_directories(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    assert "concepts/anchor-step.md" in bundle
    assert "techniques/whisk.md" in bundle
    assert "patterns/sugar-push.md" in bundle
    assert "drills/balance-drill.md" in bundle
    assert "techniques/sugar-push.md" not in bundle


def test_patterns_render_to_patterns_directory_not_techniques(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, stats = render_bundle(export, rendered_at=rendered_at, existing_log="")
    pattern_entities = [e for e in export.entities if e.kind == "pattern"]
    assert pattern_entities, "fixture must include at least one pattern entity"
    for ent in pattern_entities:
        expected_path = f"patterns/{ent.slug}.md"
        assert expected_path in bundle, (
            f"expected pattern entity {ent.slug} at {expected_path}, "
            f"got {[p for p in bundle if ent.slug in p]}"
        )
    first_pattern = pattern_entities[0]
    content = bundle[f"patterns/{first_pattern.slug}.md"]
    assert "type: pattern" in content
    assert "kind: pattern" in content


def test_pattern_count_in_log_summary(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, stats = render_bundle(export, rendered_at=rendered_at, existing_log="")
    assert stats.pattern_count >= 1
    assert "patterns" in bundle["log.md"]


def test_by_teacher_groups_and_resolves_instructors(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    concept = next(e for e in export.entities if e.slug == "anchor-step")
    indexes = build_indexes(export)
    text = render_entity_page(concept, indexes, rendered_at=rendered_at)

    assert "## By teacher" in text
    assert "### Kaiano Levine" in text
    assert "### Robert Jones" in text
    assert "Kaiano teaches anchor step" in text
    assert "Robert frames anchor step" in text


def test_null_instructor_attributes_to_source_instructors_raw(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    concept = next(e for e in export.entities if e.slug == "anchor-step")
    indexes = build_indexes(export)
    text = render_entity_page(concept, indexes, rendered_at=rendered_at)
    assert "### Robert Jones" in text


def test_empty_sections_omitted(export: WcsWikiExport, rendered_at: dt.date) -> None:
    technique = next(e for e in export.entities if e.slug == "whisk")
    indexes = build_indexes(export)
    text = render_entity_page(technique, indexes, rendered_at=rendered_at)
    assert "## Overview" not in text
    assert "## By teacher" not in text


def test_relations_render_on_both_endpoints(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    assert "## Related" in bundle["concepts/anchor-step.md"]
    assert "## Related" in bundle["techniques/whisk.md"]
    assert "prerequisite-of" in bundle["concepts/anchor-step.md"]


def test_references_render_raw_name_no_instructor_page(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    source_paths = [p for p in bundle if p.startswith("sources/")]
    source_text = "\n".join(bundle[p] for p in source_paths if "2025-03-01" in p)
    assert "## References" in source_text
    assert "**Ben** (dancer)" in source_text
    assert "instructors/ben.md" not in bundle


def test_source_pages_land_in_flat_sources_directory(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    source_paths = [path for path in bundle if path.startswith("sources/")]
    assert source_paths, "expected at least one source page"
    for path in source_paths:
        parts = path.split("/")
        assert len(parts) == 2, f"expected flat sources/ layout, got {path}"
        assert parts[0] == "sources"
        assert parts[1].endswith(".md")


def test_index_lists_all_rendered_pages(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    index = bundle["index.md"]
    assert "[[concepts/anchor-step]]" in index
    assert "[[techniques/whisk]]" in index
    assert "[[drills/balance-drill]]" in index
    assert "[[instructors/kaiano]]" in index
    assert "[[views/kaianos-canon]]" in index
    assert "[[views/kate-as-student]]" in index
    assert "[[views/full-model]]" in index
    assert "sources/kaiano/" not in index


def test_instructor_page_includes_coauth_attributions_with_null_instructor_id(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    """Co-taught sources produce attribution rows with instructor_id IS NULL.

    Those rows must still appear on each named co-instructor's page.
    """
    indexes = build_indexes(export)
    kaiano = next(i for i in export.instructors if i.slug == "kaiano")

    kaiano_attrs = export_attributions_for_instructor(kaiano, indexes)
    coauth_rows = [
        a
        for a in kaiano_attrs
        if a.instructor_id is None
        and "kaiano" in indexes.canonical_instructors_by_source.get(a.source_id, [])
    ]
    assert coauth_rows, (
        "expected at least one co-taught row on Kaiano's page after "
        "deriving from source.instructors_raw"
    )


def test_render_is_deterministic(export: WcsWikiExport, rendered_at: dt.date) -> None:
    first, _ = render_bundle(export, rendered_at=rendered_at, existing_log="# log\n")
    second, _ = render_bundle(export, rendered_at=rendered_at, existing_log="# log\n")
    assert first == second


def test_common_mistakes_and_drill_sections(
    export: WcsWikiExport, rendered_at: dt.date
) -> None:
    bundle, _ = render_bundle(export, rendered_at=rendered_at, existing_log="")
    concept = bundle["concepts/anchor-step.md"]
    drill = bundle["drills/balance-drill.md"]
    technique = bundle["techniques/whisk.md"]
    assert "## Common mistakes" in concept
    assert "Rolling through the heel" in concept
    assert "## Develops" in drill
    assert "## Requires" in technique


def _minimal_source(
    name: str,
    *,
    title: str | None = None,
    session_date: dt.date | None = None,
    session_type: str = "group_class",
    instructors_raw: list[str] | None = None,
) -> WcsSource:
    return WcsSource(
        id=_id(f"source-{name}"),
        transcript_id=_id(f"transcript-{name}"),
        title=title,
        session_date=session_date or dt.date(2025, 6, 1),
        session_type=session_type,
        instructors_raw=instructors_raw or ["Kaiano Levine"],
        students_raw=[],
        organization="",
        visibility="public",
        is_default_visible=True,
        created_at=dt.datetime(2025, 6, 1, 12, 0, tzinfo=dt.UTC),
    )


def test_dangling_relation_is_found_by_the_scan() -> None:
    """A relation pointing at an entity not in the export is reported once."""
    concept = WcsEntity(
        id=_id("entity-present"),
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="concept",
    )
    missing_id = _id("entity-missing")
    relation = WcsRelation(
        id=_id("rel-dangling"),
        from_entity_id=concept.id,
        to_entity_id=missing_id,
        relation_kind="prerequisite-of",
    )
    export = WcsWikiExport(entities=[concept], relations=[relation])
    indexes = build_indexes(export)

    dangling = find_dangling_references(export, indexes)

    assert dangling == [("missing_entity", f"relation {relation.id} to")]


def test_one_dangling_entity_is_not_counted_per_page() -> None:
    """The scan is over the data, so three pages skipping it is still one drop."""
    concept = WcsEntity(
        id=_id("entity-present"),
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="concept",
    )
    technique = WcsEntity(
        id=_id("entity-technique"),
        slug="whisk",
        canonical_name="Whisk",
        kind="technique",
    )
    missing_id = _id("entity-ghost")
    # One relation that both endpoints would skip when rendering Related —
    # plus a source page that would also skip it. Still one scan hit.
    relation = WcsRelation(
        id=_id("rel-ghost"),
        from_entity_id=concept.id,
        to_entity_id=missing_id,
        relation_kind="related-to",
        source_id=None,
    )
    export = WcsWikiExport(
        entities=[concept, technique],
        relations=[relation],
    )
    _, stats = render_bundle(export, rendered_at=dt.date(2025, 5, 28))

    missing_drops = [
        d
        for d in stats.dropped
        if d[0] == "missing_entity" and str(relation.id) in d[1]
    ]
    assert len(missing_drops) == 1


def test_colliding_source_slugs_are_reported() -> None:
    """Two untitled sources, same date, same instructor, same session type."""
    kaiano = WcsInstructor(
        id=_id("instructor-kaiano"),
        slug="kaiano",
        canonical_name="Kaiano Levine",
        aliases=["Kaiano"],
    )
    a = _minimal_source("a", title=None, instructors_raw=["Kaiano Levine"])
    b = _minimal_source("b", title=None, instructors_raw=["Kaiano Levine"])
    export = WcsWikiExport(instructors=[kaiano], sources=[a, b])

    indexes = build_indexes(export)

    assert len(indexes.slug_collisions) == 1
    slug, who = indexes.slug_collisions[0]
    assert slug == "2025-06-01-kaiano-group-class"
    assert str(a.id) in who and str(b.id) in who


def test_source_pages_differs_from_source_count_on_a_collision() -> None:
    kaiano = WcsInstructor(
        id=_id("instructor-kaiano"),
        slug="kaiano",
        canonical_name="Kaiano Levine",
        aliases=["Kaiano"],
    )
    a = _minimal_source("a", title=None, instructors_raw=["Kaiano Levine"])
    b = _minimal_source("b", title=None, instructors_raw=["Kaiano Levine"])
    export = WcsWikiExport(instructors=[kaiano], sources=[a, b])

    bundle, stats = render_bundle(export, rendered_at=dt.date(2025, 5, 28))
    source_pages = sum(1 for p in bundle if p.startswith("sources/"))

    assert stats.source_count == 2
    assert source_pages == 1
    assert any(reason == "source_slug_collision" for reason, _ in stats.dropped)


def test_unresolved_instructor_is_reported_but_still_slugified() -> None:
    """Dropping the name would lose the attribution; reporting it is the fix."""
    source = _minimal_source(
        "unknown-teacher",
        title="Mystery lesson",
        instructors_raw=["Nobody Famous"],
    )
    export = WcsWikiExport(sources=[source])

    indexes = build_indexes(export)
    _, stats = render_bundle(export, rendered_at=dt.date(2025, 5, 28))

    assert indexes.unresolved_instructors == ["Nobody Famous"]
    assert indexes.canonical_instructors_by_source[source.id] == [
        slugify("Nobody Famous")
    ]
    assert ("unresolved_instructor", "Nobody Famous") in stats.dropped
