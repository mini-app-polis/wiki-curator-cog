"""Identity fixes from notes: alias_of merges, distinct_from splits.

Merges happen when the wiki is built. These pin who absorbs whom, that the
newest claim per pair wins, that the merged-away page and its links go, and
that the linker's references follow the merge.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from unittest.mock import patch

import wiki_curator_cog.flow as flow
from wiki_curator_cog import generate, identity, link, pages
from wiki_curator_cog.render import find_broken_links, render_bundle

from .export_builders import (
    WcsAttribution,
    WcsEntity,
    WcsRelation,
    WcsSource,
    WcsWikiExport,
)


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


def _source(name: str, day: dt.date) -> WcsSource:
    return WcsSource(
        id=_id(f"s-{name}"),
        transcript_id=_id(f"t-{name}"),
        title=name,
        session_date=day,
        session_type="other",
        visibility="private",
        is_default_visible=False,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


UT = "underarm-turn"
RSP_UT = "right-side-pass-with-underarm-turn"
WHIP = "whip"


def _entities() -> list:
    return [
        WcsEntity(
            id=_id(UT),
            slug=UT,
            canonical_name="Underarm Turn",
            kind="pattern",
            aliases=["UAT"],
        ),
        WcsEntity(
            id=_id(RSP_UT),
            slug=RSP_UT,
            canonical_name="Right Side Pass with Underarm Turn",
            kind="pattern",
        ),
        WcsEntity(id=_id(WHIP), slug=WHIP, canonical_name="Whip", kind="pattern"),
    ]


def _rel(
    name: str, frm: str, to: str, kind: str, source: WcsSource | None
) -> WcsRelation:
    return WcsRelation(
        id=_id(name),
        from_entity_id=_id(frm),
        to_entity_id=_id(to),
        relation_kind=kind,
        source_id=source.id if source else None,
    )


def _export(*relations: WcsRelation, sources: list | None = None) -> WcsWikiExport:
    lesson = _source("lesson", dt.date(2026, 1, 1))
    srcs = [lesson, *(sources or [])]
    return WcsWikiExport(
        entities=_entities(),
        sources=srcs,
        attributions=[
            WcsAttribution(
                id=_id("a-ut"),
                source_id=lesson.id,
                entity_id=_id(UT),
                attribution_kind="taught",
                prose="Raise the arm over the follower's head.",
            ),
            WcsAttribution(
                id=_id("a-rsput"),
                source_id=lesson.id,
                entity_id=_id(RSP_UT),
                attribution_kind="taught",
                prose="The sternum follows the arc.",
            ),
        ],
        relations=[
            _rel("r-whip", WHIP, UT, "pattern_variant_of", lesson),
            *relations,
        ],
    )


NOTE_1 = _source("note-1", dt.date(2026, 10, 16))
NOTE_2 = _source("note-2", dt.date(2026, 11, 1))


# ── finding merges ──


def test_alias_of_merges_into_the_preferred_entity() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    merges = identity.find_merges(export)
    assert merges.by_slug == {UT: RSP_UT}


def test_hyphenated_and_spaced_kinds_are_accepted() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "Alias-Of", NOTE_1), sources=[NOTE_1])
    assert identity.find_merges(export).by_slug == {UT: RSP_UT}


def test_a_newer_distinct_from_splits_them_again() -> None:
    export = _export(
        _rel("n1", UT, RSP_UT, "alias_of", NOTE_1),
        _rel("n2", RSP_UT, UT, "distinct_from", NOTE_2),
        sources=[NOTE_1, NOTE_2],
    )
    assert not identity.find_merges(export)


def test_a_newer_alias_of_wins_over_an_older_distinct_from() -> None:
    export = _export(
        _rel("n1", UT, RSP_UT, "distinct_from", NOTE_1),
        _rel("n2", UT, RSP_UT, "alias_of", NOTE_2),
        sources=[NOTE_1, NOTE_2],
    )
    assert identity.find_merges(export).by_slug == {UT: RSP_UT}


def test_on_the_same_day_keeping_apart_wins() -> None:
    export = _export(
        _rel("n1", UT, RSP_UT, "alias_of", NOTE_1),
        _rel("n2", UT, RSP_UT, "distinct_from", NOTE_1),
        sources=[NOTE_1],
    )
    assert not identity.find_merges(export)


def test_chains_resolve_to_their_end_and_cycles_stand() -> None:
    chain = _export(
        _rel("n1", UT, RSP_UT, "alias_of", NOTE_1),
        _rel("n2", RSP_UT, WHIP, "alias_of", NOTE_1),
        sources=[NOTE_1],
    )
    assert identity.find_merges(chain).by_slug == {UT: WHIP, RSP_UT: WHIP}

    cycle = _export(
        _rel("n1", UT, RSP_UT, "alias_of", NOTE_1),
        _rel("n2", RSP_UT, WHIP, "alias_of", NOTE_1),
        _rel("n3", WHIP, UT, "alias_of", NOTE_1),
        sources=[NOTE_1],
    )
    assert not identity.find_merges(cycle)


def test_no_claims_no_merges() -> None:
    export = _export()
    merged, merges = identity.resolve(export)
    assert not merges
    assert merged is export


# ── applying them ──


def test_the_preferred_entity_absorbs_content_and_names() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    merged, _ = identity.resolve(export)

    slugs = {e.slug for e in merged.entities}
    assert UT not in slugs
    preferred = next(e for e in merged.entities if e.slug == RSP_UT)
    assert preferred.aliases == ["Underarm Turn", "UAT"]
    assert {a.entity_id for a in merged.attributions} == {_id(RSP_UT)}
    # the whip relation now points at the preferred entity; the alias_of
    # claim itself, now a self-loop, is gone
    assert [(r.from_entity_id, r.to_entity_id) for r in merged.relations] == [
        (_id(WHIP), _id(RSP_UT))
    ]


def test_the_merged_page_goes_and_nothing_links_to_it() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    merged, _ = identity.resolve(export)

    bundle, _ = render_bundle(merged, rendered_at=dt.date(2026, 10, 16))

    assert f"patterns/{UT}.md" not in bundle
    assert f"[[patterns/{RSP_UT}]]" in bundle[f"patterns/{WHIP}.md"]
    assert find_broken_links(bundle) == []
    page = bundle[f"patterns/{RSP_UT}.md"]
    assert "Raise the arm" in page and "sternum follows" in page


# ── the linker follows the merge ──


def _links_to(target_slug: str, target_id: uuid.UUID) -> link.LinksFile:
    links = link.LinksFile()
    links.statements["attribution:x"] = {
        "linker_version": link.LINKER_VERSION,
        "model": "m",
        "subject_slug": WHIP,
        "source_id": None,
        "session_date": "2026-02-01",
        "text": "",
        "references": [
            {
                "name": "underarm turn",
                "target_slug": target_slug,
                "target_entity_id": str(target_id),
                "polarity": "assert",
            }
        ],
    }
    return links


def test_references_to_a_merged_away_entity_land_on_the_preferred_one() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    merged, merges = identity.resolve(export)

    augmented, overlay, stats = link.build_overlay(
        merged, _links_to(UT, _id(UT)), merges
    )

    assert stats.proposed_pages == []  # not re-proposed as a new concept
    assert overlay.referenced_by[_id(RSP_UT)] == {_id(WHIP): 1}
    assert all(e.slug != UT for e in augmented.entities)


def test_the_catalog_resolves_a_merged_away_name_to_the_preferred_entity() -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    merged, _ = identity.resolve(export)
    ref = link.Catalog(merged.entities).resolve("underarm turn", "assert")
    assert ref is not None
    assert ref.target_slug == RSP_UT


def test_generation_sees_the_merged_evidence(tmp_path: Path) -> None:
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])
    whip_says = WcsAttribution(
        id=_id("a-whip"),
        source_id=export.sources[0].id,
        entity_id=_id(WHIP),
        attribution_kind="taught",
        prose="A whip starts like an underarm turn.",
    )
    export = export.model_copy(
        update={"attributions": [*export.attributions, whip_says]}
    )
    merged, merges = identity.resolve(export)
    links = _links_to(UT, _id(UT))
    links.statements[f"attribution:{whip_says.id}"] = links.statements.pop(
        "attribution:x"
    )
    augmented, overlay, _ = link.build_overlay(merged, links, merges)

    corpus = generate.build_corpus(augmented, overlay, links, merges)

    assert len(corpus.own[RSP_UT]) == 2
    assert UT not in corpus.entities
    assert [e.subject for e in corpus.referring[RSP_UT]] == [f"[[patterns/{WHIP}]]"]


def test_render_local_reports_merges(tmp_path: Path) -> None:
    wiki = tmp_path / "wcs-wiki"
    (wiki / "patterns").mkdir(parents=True)
    (wiki / "index.md").write_text("# index\n")
    (wiki / "patterns" / f"{UT}.md").write_text("old page\n")
    export = _export(_rel("n1", UT, RSP_UT, "alias_of", NOTE_1), sources=[NOTE_1])

    with patch.object(flow, "_fetch_export", return_value=export):
        summary = flow.render_local(wiki)

    assert summary["merged"] == {UT: RSP_UT}
    assert not (wiki / "patterns" / f"{UT}.md").exists()
    assert summary["broken_links"]["count"] == 0


# ── "also called" leaves out facet names ──

STRIKE = "strike"
FOOT_STRIKE = "foot-strike"
TOE_STRIKE = "toe-strike"
HEEL_STEP = "heel-step"
COP = "center-of-pressure"
BALL_STRIKE = "ball-strike"


def _strike_export(*relations: WcsRelation) -> WcsWikiExport:
    """The strike page as a note left it: foot strike another name for strike,
    the narrower ideas folded in as facets, some through foot strike."""
    note = _source("strike-note", dt.date(2026, 10, 19))

    def entity(slug: str, name: str, aliases: list[str]) -> WcsEntity:
        return WcsEntity(
            id=_id(slug),
            slug=slug,
            canonical_name=name,
            kind="concept",
            aliases=aliases,
        )

    return WcsWikiExport(
        entities=[
            entity(STRIKE, "Strike", []),
            entity(FOOT_STRIKE, "foot strike", ["foot-strike"]),
            entity(TOE_STRIKE, "toe strike", ["toe-first strike"]),
            entity(HEEL_STEP, "heel step", ["heel-step"]),
            entity(COP, "center of pressure", ["pronation and supination of the foot"]),
            entity(BALL_STRIKE, "ball strike", []),
        ],
        sources=[note],
        relations=[
            _rel("s1", FOOT_STRIKE, STRIKE, "alias_of", note),
            _rel("s2", TOE_STRIKE, FOOT_STRIKE, "facet_of", note),
            _rel("s3", HEEL_STEP, STRIKE, "facet_of", note),
            _rel("s4", COP, STRIKE, "facet_of", note),
            # Another name for a facet is still the facet's name.
            _rel("s5", BALL_STRIKE, TOE_STRIKE, "alias_of", note),
            *relations,
        ],
    )


def test_merges_record_the_names_facets_brought() -> None:
    merges = identity.find_merges(_strike_export())
    assert merges.facet_names[_id(STRIKE)] == {
        "toe-strike",
        "toe-first-strike",
        "heel-step",
        "center-of-pressure",
        "pronation-and-supination-of-the-foot",
        "ball-strike",
    }


def test_the_strike_page_is_also_called_only_foot_strike() -> None:
    export, merges = identity.resolve(_strike_export())
    strike = next(e for e in export.entities if e.slug == STRIKE)
    # Matching keeps every name, facets included …
    assert {"toe strike", "heel step", "center of pressure", "ball strike"} <= set(
        strike.aliases
    )
    # … but the page lists only the same-idea name.
    page = pages.build_documents(export, merges=merges)[STRIKE]
    assert page.aliases == ["foot strike"]


def test_the_markdown_export_lists_the_same_names() -> None:
    export, merges = identity.resolve(_strike_export())
    bundle, _ = render_bundle(export, rendered_at=dt.date(2026, 10, 19), merges=merges)
    frontmatter = bundle["concepts/strike.md"].split("---")[1]
    assert "foot strike" in frontmatter
    assert "toe strike" not in frontmatter


def test_a_facet_name_the_page_also_has_as_a_same_idea_name_stays() -> None:
    export = _strike_export()
    foot = next(e for e in export.entities if e.slug == FOOT_STRIKE)
    foot.aliases = ["foot-strike", "heel step"]
    export, merges = identity.resolve(export)
    page = pages.build_documents(export, merges=merges)[STRIKE]
    assert page.aliases == ["foot strike", "heel step"]


def test_a_consolidation_fold_hides_its_names_too() -> None:
    export = _strike_export()
    export.relations = [r for r in export.relations if r.id != _id("s4")]
    export, merges = identity.resolve(export, {COP: (STRIKE, True)})
    page = pages.build_documents(export, merges=merges)[STRIKE]
    assert page.aliases == ["foot strike"]


def test_a_same_name_fold_from_consolidation_stays_listed() -> None:
    export = _strike_export()
    export.relations = [r for r in export.relations if r.id != _id("s4")]
    export, merges = identity.resolve(export, {COP: (STRIKE, False)})
    page = pages.build_documents(export, merges=merges)[STRIKE]
    assert page.aliases == [
        "foot strike",
        "center of pressure",
        "pronation and supination of the foot",
    ]
