"""The linking pass, local half (ADR-005, MINI-84).

The model is faked: these tests pin what the pass does with whatever it
answers — which statements it reads, how names resolve, what the watermark
skips — not what a real model would say.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mini_app_polis.llm import LLMResult

import wiki_curator_cog.flow as flow
import wiki_curator_cog.main as main
from wiki_curator_cog import link

from .export_builders import (
    WcsAttribution,
    WcsDefinition,
    WcsDrillPurpose,
    WcsEntity,
    WcsSource,
    WcsTechniqueRequirement,
    WcsWikiExport,
)


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


def _export() -> WcsWikiExport:
    stretch = WcsEntity(
        id=_id("stretch"), slug="stretch", canonical_name="Stretch", kind="concept"
    )
    anchor_step = WcsEntity(
        id=_id("anchor-step"),
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="technique",
        aliases=["Anchor Footwork"],
    )
    drill = WcsEntity(
        id=_id("wall-drill"),
        slug="wall-drill",
        canonical_name="Wall Drill",
        kind="drill",
    )
    source = WcsSource(
        id=_id("s1"),
        transcript_id=_id("t1"),
        title="One",
        session_date=dt.date(2026, 3, 1),
        session_type="private_lesson",
        visibility="private",
        is_default_visible=False,
        created_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
    )
    return WcsWikiExport(
        entities=[stretch, anchor_step, drill],
        sources=[source],
        attributions=[
            WcsAttribution(
                id=_id("a1"),
                source_id=source.id,
                entity_id=anchor_step.id,
                attribution_kind="taught",
                raw_term="anchor step",
                prose="Keep stretch in the connection through the anchor.",
            ),
            WcsAttribution(
                id=_id("a-orphan"),
                source_id=source.id,
                entity_id=_id("missing"),
                attribution_kind="taught",
                prose="About an entity the export does not have.",
            ),
        ],
        definitions=[
            WcsDefinition(
                id=_id("d1"),
                source_id=source.id,
                entity_id=stretch.id,
                term="Stretch",
                definition="Elastic tension between partners.",
            )
        ],
        drill_purposes=[
            WcsDrillPurpose(
                id=_id("p1"),
                drill_entity_id=drill.id,
                source_id=source.id,
                skill_name="Maintain consistent stretch weight in the connected hand",
            )
        ],
        technique_requirements=[
            WcsTechniqueRequirement(
                id=_id("r1"),
                technique_entity_id=anchor_step.id,
                source_id=source.id,
                skill_name="Settle over the foot",
            ),
            WcsTechniqueRequirement(
                id=_id("r-empty"),
                technique_entity_id=anchor_step.id,
                source_id=source.id,
            ),
        ],
    )


class FakeLLM:
    """Answers each call from ``answers[statement key]``; records every call."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def generate_json(
        self, *, messages, json_schema, schema_name="output"
    ) -> LLMResult:
        payload = messages[-1].content.split("Statements (JSON):\n", 1)[1]
        keys = [s["id"] for s in json.loads(payload)]
        self.calls.append(keys)
        out = {
            "statements": [
                {"id": k, "references": self.answers.get(k, [])} for k in keys
            ]
        }
        return LLMResult(provider="fake", model="fake", output_json=out, raw_text="")


def _ref(name: str, polarity: str = "assert") -> dict[str, Any]:
    return {"name": name, "polarity": polarity}


# ── slugs ──


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Triple Step", "triple-step"),
        ("Leader's Frame", "leaders-frame"),
        ("push & pull", "push-pull"),
    ],
)
def test_slugs_match_the_api(name: str, slug: str) -> None:
    assert link.api_slugify(name) == slug


def test_depluralize_matches_the_api() -> None:
    assert link.api_depluralize("anchor-steps") == "anchor-step"
    assert link.api_depluralize("stretches") == "stretch"
    assert link.api_depluralize("pass") == "pass"


# ── statements ──


def test_every_kind_is_collected_and_unusable_ones_skipped() -> None:
    statements = link.collect_statements(_export())

    kinds = sorted(s.kind for s in statements)
    # The orphan attribution (unknown subject) and the empty requirement go.
    assert kinds == [
        "attribution",
        "definition",
        "drill_purpose",
        "technique_requirement",
    ]
    attribution = next(s for s in statements if s.kind == "attribution")
    assert attribution.subject_slug == "anchor-step"
    assert attribution.session_date == dt.date(2026, 3, 1)
    assert "stretch in the connection" in attribution.text


def test_a_sample_spreads_across_kinds_and_is_reproducible() -> None:
    statements = link.collect_statements(_export())
    first = link.sample_statements(statements, 3, seed=1)
    again = link.sample_statements(statements, 3, seed=1)

    assert [s.key for s in first] == [s.key for s in again]
    assert len({s.kind for s in first}) == 3


# ── resolution ──


@pytest.fixture
def catalog() -> link.Catalog:
    return link.Catalog(_export().entities)


def test_an_existing_name_resolves_to_its_entity(catalog: link.Catalog) -> None:
    ref = catalog.resolve("Stretch", "assert")
    assert ref is not None
    assert (ref.target_slug, ref.new) == ("stretch", False)


def test_a_general_concept_is_not_mapped_onto_a_narrower_one(
    catalog: link.Catalog,
) -> None:
    ref = catalog.resolve("anchor", "assert")
    assert ref is not None
    assert (ref.target_slug, ref.new) == ("anchor", True)


def test_names_resolve_through_aliases_and_plurals(catalog: link.Catalog) -> None:
    alias = catalog.resolve("anchor footwork", "assert")
    plural = catalog.resolve("anchor steps", "assert")
    assert alias is not None and alias.target_slug == "anchor-step"
    assert plural is not None and plural.target_slug == "anchor-step"


def test_an_unknown_general_concept_is_proposed_under_the_api_slug(
    catalog: link.Catalog,
) -> None:
    ref = catalog.resolve("Timing", "assert")
    assert ref is not None
    assert (ref.target_slug, ref.target_entity_id) == ("timing", None)


# ── a batch ──


def test_a_batch_drops_self_references_duplicates_and_invented_ids(
    catalog: link.Catalog,
) -> None:
    statements = link.collect_statements(_export())
    attribution = next(s for s in statements if s.kind == "attribution")
    llm = FakeLLM(
        {
            attribution.key: [
                _ref("anchor step"),  # its own subject
                _ref("stretch"),
                _ref("Stretch"),  # the same target again
                _ref("connection"),
                _ref("frame", polarity="deny"),
            ]
        }
    )

    out = link.link_batch(llm, [attribution], catalog)

    refs = out[attribution.key]
    assert [(r.target_slug, r.polarity, r.new) for r in refs] == [
        ("stretch", "assert", False),
        ("connection", "assert", True),
        ("frame", "deny", True),
    ]


def test_a_statement_the_model_leaves_out_stays_unlinked(catalog: link.Catalog) -> None:
    statements = link.collect_statements(_export())

    class Forgetful(FakeLLM):
        def generate_json(self, **kwargs: Any) -> LLMResult:
            return LLMResult(
                "fake",
                "fake",
                {"statements": [{"id": "invented:1", "references": []}]},
                "",
            )

    assert link.link_batch(Forgetful({}), statements, catalog) == {}


def test_names_already_in_use_are_offered_to_later_batches(
    tmp_path: Path, catalog: link.Catalog
) -> None:
    statements = link.collect_statements(_export())
    first, rest = statements[:1], statements[1:]
    llm = FakeLLM({first[0].key: [_ref("Redirect"), _ref("stretch")]})
    links = link.LinksFile()

    link.run_linking(first + rest, catalog, links, llm, model="m", batch_size=1)

    assert links.proposed_names() == ["redirect"]
    system_and_user = [
        m.content for m in link.build_messages(rest, links.proposed_names())
    ]
    assert (
        "Names already in use for concepts with no page yet:\nredirect"
        in system_and_user[1]
    )


def test_no_names_line_when_nothing_is_proposed_yet() -> None:
    statements = link.collect_statements(_export())
    _, user = link.build_messages(statements)
    assert "Names already in use" not in user.content


def test_the_prompt_asks_for_json_only_and_sends_no_catalog() -> None:
    statements = link.collect_statements(_export())
    system, user = link.build_messages(statements)

    assert "Output ONLY valid JSON" in system.content
    assert '"statements"' in system.content
    # Only the statements go out: the slug catalog was most of the cost.
    assert "Catalog" not in user.content
    assert all(s.key in user.content for s in statements)


# ── the watermark ──


def test_a_rerun_skips_what_is_linked_and_relinks_on_a_new_version(
    tmp_path: Path, catalog: link.Catalog
) -> None:
    statements = link.collect_statements(_export())
    path = tmp_path / "links.json"
    llm = FakeLLM({})

    links = link.LinksFile.load(path)
    first = link.run_linking(statements, catalog, links, llm, model="m", batch_size=2)
    links.save(path)
    assert first == {"statements": 4, "skipped": 0, "linked": 4}
    assert len(llm.calls) == 2

    links = link.LinksFile.load(path)
    second = link.run_linking(statements, catalog, links, llm, model="m")
    assert second == {"statements": 4, "skipped": 4, "linked": 0}
    assert len(llm.calls) == 2

    with patch.object(link, "LINKER_VERSION", "next"):
        third = link.run_linking(statements, catalog, links, llm, model="m")
    assert third["linked"] == 4


def test_the_links_file_round_trips_and_writes_stably(
    tmp_path: Path, catalog: link.Catalog
) -> None:
    statements = link.collect_statements(_export())
    path = tmp_path / "links.json"
    links = link.LinksFile()
    for s in statements:
        links.record(
            s,
            [link.Reference("stretch", "stretch", str(_id("stretch")), "assert")],
            "m",
        )
    links.save(path)
    before = path.read_bytes()

    link.LinksFile.load(path).save(path)

    assert path.read_bytes() == before
    entry = json.loads(before)["statements"][statements[0].key]
    assert entry["linker_version"] == link.LINKER_VERSION
    assert entry["references"][0]["target_slug"] == "stretch"


# ── review ──


def test_summary_and_review_show_new_concepts_and_denials() -> None:
    statements = link.collect_statements(_export())
    links = link.LinksFile()
    links.record(
        statements[0],
        [
            link.Reference("connection", "connection", None, "assert"),
            link.Reference("frame", "frame", str(_id("frame")), "deny"),
        ],
        "m",
    )

    summary = link.summarize(links)
    review = link.review_markdown(links, [statements[0].key])

    assert summary["new_concepts"] == {"connection": 1}
    assert summary["denials"] == 1
    assert "`connection` **(new)**" in review
    assert "**denied**" in review


# ── the command ──


def test_link_local_saves_links_and_a_review(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    statements = link.collect_statements(_export())
    attribution = next(s for s in statements if s.kind == "attribution")
    llm = FakeLLM({attribution.key: [_ref("connection")]})

    with (
        patch.object(flow, "_fetch_export", return_value=_export()),
        patch.object(link, "llm_from_env", return_value=(llm, "fake/model")),
    ):
        summary = flow.link_local(path, sample=None)

    assert summary["linked"] == 4
    assert summary["new_concepts"] == {"connection": 1}
    assert path.exists()
    assert (tmp_path / "links-review.md").exists()


def test_link_mode_runs_without_the_gate_or_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "l.json"
    with (
        patch.object(main, "load_config", side_effect=AssertionError("no config")),
        patch.object(main, "link_local", return_value={"linked": 0}) as run,
    ):
        assert main.main(["link", "--sample", "20", "--links-file", str(out)]) == 0
    run.assert_called_once_with(out.resolve(), sample=20)
    assert json.loads(capsys.readouterr().out) == {"linked": 0}
