"""Facet consolidation: narrow entities folded into broader ones (MINI-93).

These pin the model's answers being checked against the corpus, the facets
file's watermark, the fold at build time (notes outrank the model), and the
``## Details`` section the folded facets render as.
"""

from __future__ import annotations

import datetime as dt
import json
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
WHIP = "whip"

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

    def __init__(self, parents: dict[str, str | None]) -> None:
        self.parents = parents
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []

    def generate_json(self, *, messages, json_schema, schema_name="output"):
        user = messages[-1].content
        self.prompts.append(user)
        ids = [c["id"] for c in json.loads(user.split("Candidates (JSON):\n", 1)[1])]
        self.calls.append(ids)
        out = {
            "entries": [
                {"id": i, "parent": self.parents.get(i), "reason": "test"} for i in ids
            ]
        }
        return LLMResult(provider="fake", model="fake", output_json=out, raw_text="")


# ── the pass ──


def test_only_concepts_and_techniques_are_candidates() -> None:
    slugs = [c.slug for c in consolidate.candidates(_export())]
    assert slugs == [ANCHOR, DIRECTIONAL, STATIONARY]


def test_the_model_sees_definitions_and_every_possible_parent() -> None:
    llm = FakeLLM({})
    facets = consolidate.FacetsFile()
    consolidate.run_consolidation(
        _export(), facets, llm, model="fake", definitions={ANCHOR: "The settle."}
    )
    prompt = llm.prompts[0]
    assert "Anchor; Anchor with Directional Intent; Stationary Anchor" in prompt
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
    assert counts["decided"] == 3
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
    assert counts["skipped"] == 3
    assert loaded.parents() == {STATIONARY: ANCHOR}

    monkeypatch.setattr(consolidate, "CONSOLIDATOR_VERSION", "next")
    third = FakeLLM({})
    consolidate.run_consolidation(_export(), loaded, third, model="fake")
    assert sorted(third.calls[0]) == [ANCHOR, DIRECTIONAL, STATIONARY]


def test_a_failed_batch_leaves_its_entities_undecided() -> None:
    class Fails(FakeLLM):
        def generate_json(self, **kwargs):
            raise LLMError("bad json")

    facets = consolidate.FacetsFile()
    counts = consolidate.run_consolidation(_export(), facets, Fails({}), model="fake")
    assert counts["failed_batches"] == 1
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


# ── the fold ──


def test_facets_fold_into_the_parent_and_are_listed() -> None:
    export, merges = identity.resolve(_export(), {STATIONARY: ANCHOR})
    slugs = {e.slug for e in export.entities}
    assert STATIONARY not in slugs
    anchor = next(e for e in export.entities if e.slug == ANCHOR)
    assert "Stationary Anchor" in anchor.aliases
    assert [a.entity_id for a in export.attributions] == [_id(ANCHOR)]
    assert merges.facets[_id(ANCHOR)][0].name == "Stationary Anchor"
    # the whip now ends in the anchor
    assert any(r.to_entity_id == _id(ANCHOR) for r in export.relations)


def test_drills_and_patterns_are_never_folded_by_the_model() -> None:
    merges = identity.find_merges(_export(), {DRILL: ANCHOR, STATIONARY: WHIP})
    assert not merges


def test_a_note_keeping_them_apart_outranks_the_model() -> None:
    export = _export(_note(STATIONARY, ANCHOR, "distinct_from"))
    assert not identity.find_merges(export, {STATIONARY: ANCHOR})


def test_a_note_folding_elsewhere_outranks_the_model() -> None:
    export = _export(_note(STATIONARY, DIRECTIONAL, "facet_of"))
    merges = identity.find_merges(export, {STATIONARY: ANCHOR})
    assert merges.by_slug == {STATIONARY: DIRECTIONAL}
    assert [f.slug for f in merges.facets[_id(DIRECTIONAL)]] == [STATIONARY]


def test_a_note_can_fold_any_kind() -> None:
    merges = identity.find_merges(_export(_note(DRILL, ANCHOR, "facet_of")))
    assert merges.by_slug == {DRILL: ANCHOR}


def test_facet_chains_resolve_to_the_broadest_page() -> None:
    merges = identity.find_merges(
        _export(), {STATIONARY: DIRECTIONAL, DIRECTIONAL: ANCHOR}
    )
    assert merges.by_slug == {STATIONARY: ANCHOR, DIRECTIONAL: ANCHOR}
    assert [f.slug for f in merges.facets[_id(ANCHOR)]] == [DIRECTIONAL, STATIONARY]


def test_a_facet_cycle_leaves_both_standing() -> None:
    assert not identity.find_merges(_export(), {STATIONARY: ANCHOR, ANCHOR: STATIONARY})


def test_an_alias_is_not_listed_as_a_facet() -> None:
    merges = identity.find_merges(_export(_note(STATIONARY, ANCHOR, "alias_of")))
    assert merges.by_slug == {STATIONARY: ANCHOR}
    assert merges.facets == {}


# ── the page ──


def test_details_list_each_facet_with_its_gist_and_links_follow() -> None:
    export, merges = identity.resolve(_export(), {STATIONARY: ANCHOR})
    prose = generate.ProseFile()
    details = generate.details_by_entity(export, merges, prose)
    assert details == {
        _id(ANCHOR): [("Stationary Anchor", "An anchor that stays in place.")]
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
    export, merges = identity.resolve(_export(), {STATIONARY: ANCHOR})
    pages = generate.PageIndex.from_export(export, merges)
    assert (
        pages.repair("see [[concepts/stationary-anchor|it]]")
        == "see [[concepts/anchor|it]]"
    )


def test_the_linker_resolves_a_facet_name_to_its_parent() -> None:
    export, _ = identity.resolve(_export(), {STATIONARY: ANCHOR})
    ref = link.Catalog(export.entities).resolve("stationary anchor", "assert")
    assert ref is not None and ref.target_slug == ANCHOR
