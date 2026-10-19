"""Facet consolidation: narrow entities folded into broader ones (MINI-93).

These pin the model's answers being checked against the corpus, the facets
file's watermark, the fold at build time (notes outrank the model), and the
``## Details`` section the folded facets render as.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from mini_app_polis.llm import LLMResult
from mini_app_polis.llm.errors import LLMError

from wiki_curator_cog import consolidate, generate, identity, link
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


ANCHOR = "anchor"
STATIONARY = "stationary-anchor"
DIRECTIONAL = "anchor-with-directional-intent"
DRILL = "anchor-drill"
WALL_DRILL = "anchor-drill-against-the-wall"
WHIP = "whip"
REVERSE_WHIP = "reverse-whip"
WHIP_TURN = "whip-turn"

LESSON = WcsSource(
    id=_id("s-lesson"),
    transcript_id=_id("t-lesson"),
    title="lesson",
    session_date=dt.date(2026, 1, 1),
    session_type="private",
    visibility="private",
    is_default_visible=False,
    created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
)
NOTE = WcsSource(
    id=_id("s-note"),
    transcript_id=_id("t-note"),
    title="note",
    session_date=dt.date(2026, 10, 16),
    session_type="other",
    visibility="private",
    is_default_visible=False,
    created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
)


def _entity(slug: str, name: str, kind: str = "concept", overview: str = "") -> Any:
    return WcsEntity(
        id=_id(slug), slug=slug, canonical_name=name, kind=kind, overview_md=overview
    )


def _export(*relations: WcsRelation) -> WcsWikiExport:
    return WcsWikiExport(
        entities=[
            _entity(ANCHOR, "Anchor", overview="The settling at the end of a pattern."),
            _entity(
                STATIONARY,
                "Stationary Anchor",
                overview="An anchor that stays in place. Both partners settle.",
            ),
            _entity(DIRECTIONAL, "Anchor with Directional Intent", kind="technique"),
            _entity(DRILL, "Anchor Drill", kind="drill"),
            _entity(WHIP, "Whip", kind="pattern"),
        ],
        sources=[LESSON, NOTE],
        attributions=[
            *(
                WcsAttribution(
                    id=_id(f"a-anchor-{src.title}"),
                    source_id=src.id,
                    entity_id=_id(ANCHOR),
                    attribution_kind="taught",
                    prose="Settle and stop.",
                )
                for src in (LESSON, NOTE)
            ),
            WcsAttribution(
                id=_id("a-stationary"),
                source_id=LESSON.id,
                entity_id=_id(STATIONARY),
                attribution_kind="taught",
                prose="Stay over your feet.",
            ),
        ],
        relations=[
            WcsRelation(
                id=_id("r-whip"),
                from_entity_id=_id(WHIP),
                to_entity_id=_id(STATIONARY),
                relation_kind="ends_in",
                source_id=LESSON.id,
            ),
            *relations,
        ],
    )


def _with_more() -> WcsWikiExport:
    """The export plus a second drill and two more patterns.

    ``anchor drill`` and ``whip`` each get a source, so they outweigh the
    newcomers and can be their parent.
    """
    base = _export()
    return base.model_copy(
        update={
            "entities": [
                *base.entities,
                _entity(WALL_DRILL, "Anchor Drill Against the Wall", kind="drill"),
                _entity(REVERSE_WHIP, "Reverse Whip", kind="pattern"),
                _entity(WHIP_TURN, "Whip Turn", kind="pattern"),
            ],
            "attributions": [
                *base.attributions,
                *(
                    WcsAttribution(
                        id=_id(f"a-{slug}"),
                        source_id=LESSON.id,
                        entity_id=_id(slug),
                        attribution_kind="drilled" if slug == DRILL else "taught",
                        prose="Practise it.",
                    )
                    for slug in (DRILL, WHIP)
                ),
            ],
        }
    )


def _prompt_for(llm: FakeLLM, slug: str) -> tuple[str, str]:
    """(system, user) of the call that carried ``slug`` as a candidate."""
    i = next(n for n, ids in enumerate(llm.calls) if slug in ids)
    return llm.systems[i], llm.prompts[i]


def _note(frm: str, to: str, kind: str) -> WcsRelation:
    return WcsRelation(
        id=_id(f"n-{frm}-{to}-{kind}"),
        from_entity_id=_id(frm),
        to_entity_id=_id(to),
        relation_kind=kind,
        source_id=NOTE.id,
    )


class FakeLLM:
    """Answers from ``parents[slug]`` (a name or None); records each batch."""

    def __init__(
        self, parents: dict[str, str | None], same: tuple[str, ...] = ()
    ) -> None:
        self.parents = parents
        self.same = set(same)
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self._lock = threading.Lock()

    def generate_json(self, *, messages, json_schema, schema_name="output"):
        user = messages[-1].content
        ids = [c["id"] for c in json.loads(user.split("Candidates (JSON):\n", 1)[1])]
        with self._lock:  # batches run on worker threads
            self.prompts.append(user)
            self.systems.append(messages[0].content)
            self.calls.append(ids)
        out = {
            "entries": [
                {
                    "id": i,
                    "fold": self.parents.get(i) is not None,
                    "parent": self.parents.get(i),
                    "same": i in self.same,
                    "reason": "test",
                }
                for i in ids
            ]
        }
        return LLMResult(provider="fake", model="fake", output_json=out, raw_text="")


# ── the pass ──


def test_every_foldable_kind_is_a_candidate() -> None:
    slugs = [c.slug for c in consolidate.candidates(_export())]
    assert slugs == [ANCHOR, DRILL, DIRECTIONAL, STATIONARY, WHIP]


def test_the_model_sees_definitions_and_every_possible_parent() -> None:
    llm = FakeLLM({})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(
        _export(), facets, llm, model="fake", definitions={ANCHOR: "The settle."}
    )
    system, prompt = _prompt_for(llm, ANCHOR)
    assert system == consolidate.SYSTEM_PROMPT
    assert (
        "Anchor [2]; Anchor with Directional Intent [0]; Stationary Anchor [1]"
        in prompt
    )
    assert '"sources": 1' in prompt
    assert "The settle." in prompt
    assert "Whip" not in prompt.split("Candidates", 1)[0]


def test_parents_are_resolved_and_bad_answers_mean_standalone() -> None:
    llm = FakeLLM(
        {
            STATIONARY: " ANCHOR ",  # matched the way the API matches names
            DIRECTIONAL: "nothing like this exists",
            ANCHOR: "Anchor",  # itself
        }
    )
    facets = consolidate.FacetsFile()
    counts = consolidate.run_consolidation(_export(), facets, llm, model="fake")
    assert counts["decided"] == 5
    assert facets.parents() == {STATIONARY: ANCHOR}
    assert facets.entities[DIRECTIONAL]["parent"] is None


def test_decided_entities_are_skipped_until_the_version_changes(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "facets.json"
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(
        _export(), facets, FakeLLM({STATIONARY: "anchor"}), model="fake"
    )
    facets.save(path)

    again = FakeLLM({})
    loaded = consolidate.FacetsFile.load(path)
    counts = consolidate.run_consolidation(_export(), loaded, again, model="fake")
    assert again.calls == []
    assert counts["skipped"] == 5
    assert loaded.parents() == {STATIONARY: ANCHOR}

    # A bump redecides only its own group: the drill and pattern stay decided.
    monkeypatch.setitem(consolidate.CONSOLIDATOR_VERSIONS, "concept", "next")
    third = FakeLLM({})
    counts = consolidate.run_consolidation(_export(), loaded, third, model="fake")
    assert sorted(sum(third.calls, [])) == [ANCHOR, DIRECTIONAL, STATIONARY]
    assert counts["pending_by_group"] == {"concept": 3, "drill": 0, "pattern": 0}
    assert loaded.entities[ANCHOR]["version"] == "next"
    assert loaded.entities[DRILL]["version"] == "5"

    monkeypatch.setitem(consolidate.CONSOLIDATOR_VERSIONS, "drill", "next")
    fourth = FakeLLM({})
    consolidate.run_consolidation(_export(), loaded, fourth, model="fake")
    assert fourth.calls == [[DRILL]]


def test_a_failed_batch_leaves_its_entities_undecided() -> None:
    class Fails(FakeLLM):
        def generate_json(self, **kwargs):
            raise LLMError("bad json")

    facets = consolidate.FacetsFile()
    counts = consolidate.run_consolidation(_export(), facets, Fails({}), model="fake")
    assert counts["failed_batches"] == 3  # one per kind group
    assert facets.entities == {}


def test_review_groups_facets_under_their_parent() -> None:
    facets = consolidate.FacetsFile()
    facets.record(STATIONARY, ANCHOR, "a kind of anchor", "fake")
    facets.record(DIRECTIONAL, ANCHOR, "", "fake")
    text = consolidate.review_markdown(
        facets, {ANCHOR: "Anchor", STATIONARY: "Stationary Anchor"}
    )
    assert "## Anchor (2)" in text
    assert "- Stationary Anchor — a kind of anchor" in text
    assert text.startswith("# Facet review — versions concept 5, drill 5, pattern 5")


# ── the fold ──


def test_facets_fold_into_the_parent_and_are_listed() -> None:
    export, merges = identity.resolve(_export(), {STATIONARY: (ANCHOR, True)})
    slugs = {e.slug for e in export.entities}
    assert STATIONARY not in slugs
    anchor = next(e for e in export.entities if e.slug == ANCHOR)
    assert "Stationary Anchor" in anchor.aliases
    assert {a.entity_id for a in export.attributions} == {_id(ANCHOR)}
    assert merges.facets[_id(ANCHOR)][0].name == "Stationary Anchor"
    # the whip now ends in the anchor
    assert any(r.to_entity_id == _id(ANCHOR) for r in export.relations)


def test_nothing_folds_across_kind_groups() -> None:
    merges = identity.find_merges(
        _export(),
        {DRILL: (ANCHOR, True), STATIONARY: (WHIP, True), WHIP: (DRILL, False)},
    )
    assert not merges


def test_a_drill_folds_into_another_drill_as_a_facet() -> None:
    export, merges = identity.resolve(_with_more(), {WALL_DRILL: (DRILL, True)})
    assert merges.by_slug == {WALL_DRILL: DRILL}
    assert [f.slug for f in merges.facets[_id(DRILL)]] == [WALL_DRILL]
    details = generate.details_by_entity(export, merges)
    bundle, _ = render_bundle(
        export, rendered_at=dt.date(2026, 10, 16), details=details
    )
    assert "**Anchor Drill Against the Wall**" in bundle["drills/anchor-drill.md"]
    assert "drills/anchor-drill-against-the-wall.md" not in bundle
    assert find_broken_links(bundle) == []


def test_a_pattern_folds_only_as_another_name() -> None:
    assert not identity.find_merges(_with_more(), {REVERSE_WHIP: (WHIP, True)})
    export, merges = identity.resolve(_with_more(), {WHIP_TURN: (WHIP, False)})
    assert merges.by_slug == {WHIP_TURN: WHIP}
    assert merges.facets == {}
    whip = next(e for e in export.entities if e.slug == WHIP)
    assert "Whip Turn" in whip.aliases


def test_each_group_has_its_own_prompt_and_parents() -> None:
    llm = FakeLLM({})
    consolidate.run_consolidation(
        _with_more(), consolidate.FacetsFile(), llm, model="fake"
    )
    assert sorted(map(sorted, llm.calls)) == sorted(
        [
            [ANCHOR, DIRECTIONAL, STATIONARY],
            [DRILL, WALL_DRILL],
            [REVERSE_WHIP, WHIP, WHIP_TURN],
        ]
    )
    system, user = _prompt_for(llm, DRILL)
    assert system == consolidate.DRILL_PROMPT
    names = user.split("Candidates", 1)[0]
    assert "Anchor Drill [1]; Anchor Drill Against the Wall [0]" in names
    assert "Stationary Anchor" not in names and "Whip" not in names
    system, user = _prompt_for(llm, WHIP)
    assert system == consolidate.PATTERN_PROMPT
    assert "Reverse Whip [0]; Whip [1]; Whip Turn [0]" in user


def test_the_model_cannot_fold_across_groups() -> None:
    llm = FakeLLM({WALL_DRILL: "anchor", REVERSE_WHIP: "anchor drill"})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_with_more(), facets, llm, model="fake")
    assert facets.parents() == {}


def test_a_pattern_variation_is_refused_and_a_duplicate_folds() -> None:
    llm = FakeLLM({REVERSE_WHIP: "whip", WHIP_TURN: "whip"}, same=(WHIP_TURN,))
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_with_more(), facets, llm, model="fake")
    assert facets.folds() == {WHIP_TURN: (WHIP, False)}
    assert facets.entities[REVERSE_WHIP]["parent"] is None
    assert "only as the same one" in facets.entities[REVERSE_WHIP]["reason"]


def test_a_drill_variation_folds_and_is_listed() -> None:
    llm = FakeLLM({WALL_DRILL: "Anchor Drill [1]"})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_with_more(), facets, llm, model="fake")
    assert facets.folds() == {WALL_DRILL: (DRILL, True)}


def test_a_note_keeping_them_apart_outranks_the_model() -> None:
    export = _export(_note(STATIONARY, ANCHOR, "distinct_from"))
    assert not identity.find_merges(export, {STATIONARY: (ANCHOR, True)})


def test_a_note_folding_elsewhere_outranks_the_model() -> None:
    export = _export(_note(STATIONARY, DIRECTIONAL, "facet_of"))
    merges = identity.find_merges(export, {STATIONARY: (ANCHOR, True)})
    assert merges.by_slug == {STATIONARY: DIRECTIONAL}
    assert [f.slug for f in merges.facets[_id(DIRECTIONAL)]] == [STATIONARY]


def test_a_note_can_fold_any_kind() -> None:
    merges = identity.find_merges(_export(_note(DRILL, ANCHOR, "facet_of")))
    assert merges.by_slug == {DRILL: ANCHOR}


def test_facet_chains_resolve_to_the_broadest_page() -> None:
    merges = identity.find_merges(
        _export(), {STATIONARY: (DIRECTIONAL, True), DIRECTIONAL: (ANCHOR, True)}
    )
    assert merges.by_slug == {STATIONARY: ANCHOR, DIRECTIONAL: ANCHOR}
    assert [f.slug for f in merges.facets[_id(ANCHOR)]] == [DIRECTIONAL, STATIONARY]


def test_a_facet_cycle_leaves_both_standing() -> None:
    assert not identity.find_merges(
        _export(), {STATIONARY: (ANCHOR, True), ANCHOR: (STATIONARY, True)}
    )


def test_an_alias_is_not_listed_as_a_facet() -> None:
    merges = identity.find_merges(_export(_note(STATIONARY, ANCHOR, "alias_of")))
    assert merges.by_slug == {STATIONARY: ANCHOR}
    assert merges.facets == {}


# ── the page ──


def test_details_list_each_facet_with_its_gist_and_links_follow() -> None:
    export, merges = identity.resolve(_export(), {STATIONARY: (ANCHOR, True)})
    prose = generate.ProseFile()
    details = generate.details_by_entity(export, merges, prose)
    assert details == {
        _id(ANCHOR): [("Stationary Anchor", "An anchor that stays in place.", 0)]
    }

    bundle, _ = render_bundle(
        export, rendered_at=dt.date(2026, 10, 16), details=details
    )
    page = bundle["concepts/anchor.md"]
    assert "## Details" in page
    assert "- **Stationary Anchor** — An anchor that stays in place." in page
    assert "concepts/stationary-anchor.md" not in bundle
    assert find_broken_links(bundle) == []


def test_gists_are_one_sentence_capped_in_length() -> None:
    long = " ".join(["word"] * 50) + ". Second."
    assert generate._gist(long).endswith("…")
    assert len(generate._gist(long).split()) == generate.DETAIL_MAX_WORDS
    assert generate._gist("One. Two.") == "One."


def test_links_to_a_folded_page_are_repaired_to_its_parent() -> None:
    export, merges = identity.resolve(_export(), {STATIONARY: (ANCHOR, True)})
    pages = generate.PageIndex.from_export(export, merges)
    assert (
        pages.repair("see [[concepts/stationary-anchor|it]]")
        == "see [[concepts/anchor|it]]"
    )


def test_the_linker_resolves_a_facet_name_to_its_parent() -> None:
    export, _ = identity.resolve(_export(), {STATIONARY: (ANCHOR, True)})
    ref = link.Catalog(export.entities).resolve("stationary anchor", "assert")
    assert ref is not None and ref.target_slug == ANCHOR


def test_a_null_or_missing_field_costs_only_that_entry() -> None:
    class Sloppy(FakeLLM):
        def generate_json(self, **kwargs):
            out = {
                "entries": [
                    {
                        "id": STATIONARY,
                        "fold": True,
                        "parent": "anchor",
                        "reason": None,
                    },
                    {"id": DIRECTIONAL},
                ]
            }
            return LLMResult(provider="f", model="f", output_json=out, raw_text="")

    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_export(), facets, Sloppy({}), model="fake")
    assert facets.parents() == {STATIONARY: ANCHOR}
    assert facets.entities[STATIONARY]["reason"] == ""
    assert facets.entities[DIRECTIONAL]["parent"] is None


def test_the_same_idea_folds_as_another_name_with_no_details_line() -> None:
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(
        _export(),
        facets,
        FakeLLM({STATIONARY: "anchor", DIRECTIONAL: "anchor"}, same=(STATIONARY,)),
        model="fake",
    )
    export, merges = identity.resolve(_export(), facets.folds())
    assert merges.by_slug == {STATIONARY: ANCHOR, DIRECTIONAL: ANCHOR}
    assert [f.slug for f in merges.facets[_id(ANCHOR)]] == [DIRECTIONAL]
    anchor = next(e for e in export.entities if e.slug == ANCHOR)
    assert "Stationary Anchor" in anchor.aliases


def test_same_is_ignored_without_a_parent() -> None:
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(
        _export(), facets, FakeLLM({}, same=(STATIONARY,)), model="fake"
    )
    assert facets.entities[STATIONARY]["same"] is False


def test_review_follows_chains_to_the_page_they_end_on() -> None:
    facets = consolidate.FacetsFile()
    facets.record(STATIONARY, DIRECTIONAL, "", "fake", same=True)
    facets.record(DIRECTIONAL, ANCHOR, "", "fake")
    text = consolidate.review_markdown(
        facets,
        {ANCHOR: "Anchor", STATIONARY: "Stationary Anchor", DIRECTIONAL: "Directional"},
    )
    assert "## Anchor (2)" in text
    assert "- Stationary Anchor (via Directional) _(same; another name)_" in text
    assert "## Directional" not in text


# ── support ──


def test_support_counts_sources_and_references_from_elsewhere() -> None:
    links = link.LinksFile()
    links.statements = {
        "attribution:x": {
            "subject_slug": DIRECTIONAL,
            "session_date": "2026-01-01",
            "references": [
                {
                    "name": "anchor",
                    "target_slug": ANCHOR,
                    "target_entity_id": None,
                    "polarity": "assert",
                },
            ],
        },
        "attribution:y": {
            "subject_slug": STATIONARY,
            "session_date": "2026-01-01",
            "references": [
                {
                    "name": "anchor",
                    "target_slug": ANCHOR,
                    "target_entity_id": None,
                    "polarity": "assert",
                },
            ],
        },
    }
    supports = consolidate.support(_export(), links)
    assert supports[ANCHOR] == consolidate.Support(sources=2, references=2)
    assert supports[STATIONARY] == consolidate.Support(sources=1, references=0)
    assert supports[STATIONARY].thin and not supports[ANCHOR].thin


def test_a_thinner_parent_is_refused() -> None:
    llm = FakeLLM({ANCHOR: "stationary anchor [1]"})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_export(), facets, llm, model="fake")
    assert facets.entities[ANCHOR]["parent"] is None
    assert "less support" in facets.entities[ANCHOR]["reason"]


def test_a_bracketed_count_in_the_answer_still_resolves() -> None:
    llm = FakeLLM({STATIONARY: "Anchor [2]"})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_export(), facets, llm, model="fake")
    assert facets.parents() == {STATIONARY: ANCHOR}


def test_review_shows_evidence_and_lists_thin_entries_with_no_home() -> None:
    facets = consolidate.FacetsFile()
    facets.record(STATIONARY, ANCHOR, "", "fake")
    facets.record(DIRECTIONAL, None, "", "fake")
    supports = consolidate.support(_export())
    text = consolidate.review_markdown(
        facets,
        {ANCHOR: "Anchor", STATIONARY: "Stationary Anchor", DIRECTIONAL: "Directional"},
        supports,
    )
    assert "## Anchor `2s/0r` (1)" in text
    assert "- Stationary Anchor `1s/0r`" in text
    assert "## Thin and standalone (1)" in text
    assert "- Directional `0s/0r`" in text


def test_a_parent_without_an_explicit_fold_is_standalone() -> None:
    class Contradicts(FakeLLM):
        def generate_json(self, **kwargs):
            out = {
                "entries": [
                    {"id": STATIONARY, "reason": "keep standalone", "parent": "anchor"},
                    {"id": DIRECTIONAL, "fold": False, "parent": "anchor"},
                ]
            }
            return LLMResult(provider="f", model="f", output_json=out, raw_text="")

    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(_export(), facets, Contradicts({}), model="fake")
    assert facets.parents() == {}


def test_a_facet_folded_through_another_nests_under_it() -> None:
    export, merges = identity.resolve(
        _export(), {STATIONARY: (DIRECTIONAL, True), DIRECTIONAL: (ANCHOR, True)}
    )
    by_slug = {f.slug: f for f in merges.facets[_id(ANCHOR)]}
    assert by_slug[STATIONARY].under == DIRECTIONAL
    assert by_slug[DIRECTIONAL].under is None

    details = generate.details_by_entity(export, merges)[_id(ANCHOR)]
    assert [(name, depth) for name, _, depth in details] == [
        ("Anchor with Directional Intent", 0),
        ("Stationary Anchor", 1),
    ]
    bundle, _ = render_bundle(
        export, rendered_at=dt.date(2026, 10, 16), details={_id(ANCHOR): details}
    )
    assert (
        "\n  - **Stationary Anchor** — An anchor that stays in place."
        in bundle["concepts/anchor.md"]
    )


def test_a_facet_through_an_alias_is_not_nested() -> None:
    _, merges = identity.resolve(
        _export(), {STATIONARY: (DIRECTIONAL, True), DIRECTIONAL: (ANCHOR, False)}
    )
    assert [(f.slug, f.under) for f in merges.facets[_id(ANCHOR)]] == [
        (STATIONARY, None)
    ]


def test_thin_review_shows_what_was_said_where_and_where_it_went() -> None:
    facets = consolidate.FacetsFile()
    facets.record(STATIONARY, ANCHOR, "", "fake")
    facets.record(DIRECTIONAL, None, "", "fake")
    facets.record(ANCHOR, None, "", "fake")
    supports = consolidate.support(_export())
    text = consolidate.thin_review_markdown(
        _export(), facets, supports, {DIRECTIONAL: "Intent kept through the anchor."}
    )
    own, folded = text.split("## Folded into a broader page", 1)
    assert "## On their own page (1)" in own
    assert "### Anchor with Directional Intent" in own
    assert "**Definition.** Intent kept through the anchor." in own
    assert "### Stationary Anchor" in folded
    assert (
        "folded into **Anchor** · [concepts/anchor.md](../concepts/anchor.md)" in folded
    )
    assert "> Stay over your feet.\n> — 2026-01-01 · lesson" in folded
    # anchor has two sources: not thin
    assert "### Anchor\n" not in text


def test_the_thin_list_is_what_the_review_shows() -> None:
    """The API's thin list (MINI-101) and thin-review.md are one selection."""
    facets = consolidate.FacetsFile()
    facets.record(STATIONARY, ANCHOR, "", "fake")
    facets.record(DIRECTIONAL, None, "", "fake")
    facets.record(ANCHOR, None, "", "fake")
    supports = consolidate.support(_export())
    definitions = {DIRECTIONAL: "Intent kept through the anchor."}
    thin = consolidate.thin_entities(_export(), facets, supports, definitions)
    text = consolidate.thin_review_markdown(_export(), facets, supports, definitions)

    # the review groups them (own page first); the list is by name
    assert {f"### {t.name}" for t in thin} == {
        line for line in text.splitlines() if line.startswith("### ")
    }
    by_slug = {t.slug: t for t in thin}
    assert set(by_slug) == {DIRECTIONAL, STATIONARY}
    assert by_slug[DIRECTIONAL].page == DIRECTIONAL
    assert by_slug[DIRECTIONAL].definition == "Intent kept through the anchor."
    stationary = by_slug[STATIONARY]
    assert (stationary.page, stationary.same) == (ANCHOR, False)
    assert stationary.sources == 1
    quote = stationary.quotes[0]
    assert quote.text == "Stay over your feet."
    assert (quote.session_date, quote.source_title) == (dt.date(2026, 1, 1), "lesson")
    assert quote.source_id is not None
