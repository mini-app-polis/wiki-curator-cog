"""Generation, local half (ADR-005, MINI-86).

The model is faked. These pin what generation does around it: which
evidence an entity gets, the watermark, the page as the base, and the
render of the result.
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
from wiki_curator_cog import generate, link
from wiki_curator_cog import markdown_utils as md
from wiki_curator_cog.render import render_bundle

from .export_builders import (
    WcsAttribution,
    WcsDefinition,
    WcsEntity,
    WcsInstructor,
    WcsSource,
    WcsWikiExport,
)


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


def _source(name: str, day: dt.date) -> Any:
    return WcsSource(
        id=_id(f"s-{name}"),
        transcript_id=_id(f"t-{name}"),
        title=name,
        session_date=day,
        session_type="private_lesson",
        instructors_raw=["Kaiano"],
        visibility="private",
        is_default_visible=False,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


def _export() -> WcsWikiExport:
    anchor = WcsEntity(
        id=_id("anchor-step"),
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="technique",
    )
    stretch = WcsEntity(
        id=_id("stretch"), slug="stretch", canonical_name="Stretch", kind="concept"
    )
    old, new = _source("Old", dt.date(2025, 1, 1)), _source("New", dt.date(2026, 6, 1))
    robert = WcsInstructor(
        id=_id("robert"), slug="robert-royston", canonical_name="Robert Royston"
    )
    return WcsWikiExport(
        entities=[anchor, stretch],
        instructors=[robert],
        sources=[old, new],
        attributions=[
            WcsAttribution(
                id=_id("a-old"),
                source_id=old.id,
                entity_id=anchor.id,
                attribution_kind="taught",
                prose="The anchor travels backward.",
            ),
            WcsAttribution(
                id=_id("a-new"),
                source_id=new.id,
                entity_id=anchor.id,
                instructor_id=robert.id,
                attribution_kind="taught",
                prose="The anchor is an arrival, with stretch in the connection.",
            ),
        ],
        definitions=[
            WcsDefinition(
                id=_id("d-stretch"),
                source_id=new.id,
                entity_id=stretch.id,
                term="Stretch",
                definition="Elastic tension between partners.",
            )
        ],
    )


def _links(export: WcsWikiExport) -> link.LinksFile:
    """The new anchor statement mentions stretch and connection (proposed)."""
    links = link.LinksFile()
    statement = next(
        s
        for s in link.collect_statements(export)
        if s.key == f"attribution:{_id('a-new')}"
    )
    links.record(
        statement,
        [
            link.Reference("stretch", "stretch", str(_id("stretch")), "assert"),
            link.Reference("connection", "connection", None, "assert"),
        ],
        "m",
    )
    return links


class FakeLLM:
    """Returns an overview naming the statement ids it was given."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self, *, messages, json_schema, schema_name="output"
    ) -> LLMResult:
        user = messages[-1].content
        statements = json.loads(
            user.split("New statements (JSON, newest first):\n", 1)[1]
        )
        base = user.split("Current definition:\n", 1)[1].split(
            "\n\nCurrent context", 1
        )[0]
        self.calls.append({"base": base, "statements": statements})
        n = len(statements)
        out = {
            "definition": f"Definition from {n} statements.",
            "context": f"Context from {n}.",
        }
        return LLMResult("fake", "fake", out, "")


def _corpus_and_overlay():
    export = _export()
    links = _links(export)
    augmented, overlay, _ = link.build_overlay(export, links)
    return augmented, generate.build_corpus(augmented, overlay, links)


# ── evidence ──


def test_own_and_referring_evidence_are_gathered() -> None:
    _, corpus = _corpus_and_overlay()

    assert {e.key for e in corpus.own["anchor-step"]} == {
        f"attribution:{_id('a-old')}",
        f"attribution:{_id('a-new')}",
    }
    # stretch is mentioned by the anchor statement; connection exists only
    # because the linker proposed it, and still gets its evidence.
    assert [e.subject for e in corpus.referring["stretch"]] == [
        "[[techniques/anchor-step]]"
    ]
    assert [e.role for e in corpus.referring["connection"]] == ["refers"]


def test_evidence_carries_teacher_date_and_source_link() -> None:
    _, corpus = _corpus_and_overlay()
    new = next(e for e in corpus.own["anchor-step"] if "arrival" in e.text)
    old = next(e for e in corpus.own["anchor-step"] if "backward" in e.text)

    assert new.teacher == "Robert Royston"
    assert old.teacher == "Kaiano"  # no instructor id: the source's instructors
    assert new.date == "2026-06-01"
    assert new.as_prompt()["source"].startswith("[[sources/2026-06-01")


def test_a_denied_reference_is_not_evidence() -> None:
    export = _export()
    links = _links(export)
    # A newer statement about anchor step denies its link to stretch.
    newer = link.Statement(
        kind="attribution",
        id=_id("a-deny"),
        subject_id=_id("anchor-step"),
        subject_slug="anchor-step",
        source_id=None,
        session_date=dt.date(2027, 1, 1),
        text="Anchor step has nothing to do with stretch.",
    )
    links.record(
        newer, [link.Reference("stretch", "stretch", str(_id("stretch")), "deny")], "m"
    )
    augmented, overlay, _ = link.build_overlay(export, links)
    corpus = generate.build_corpus(augmented, overlay, links)

    assert "stretch" not in corpus.referring


def test_evidence_is_newest_first_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _, corpus = _corpus_and_overlay()
    monkeypatch.setattr(generate, "MAX_OWN_STATEMENTS", 1)
    entity = corpus.entities["anchor-step"]

    pending = generate.pending(entity, corpus, generate.ProseFile())

    assert [e.date for e in pending] == ["2026-06-01"]


# ── a run, and the watermark ──


def test_a_run_integrates_then_skips_until_there_is_something_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, corpus = _corpus_and_overlay()
    monkeypatch.setattr(generate, "MAX_OWN_STATEMENTS", 1)
    prose = generate.ProseFile()
    llm = FakeLLM()
    anchor = corpus.entities["anchor-step"]

    generate.run_generation([anchor], corpus, prose, llm, model="m", today="2026-10-16")
    assert prose.definition("anchor-step") == "Definition from 1 statements."
    assert llm.calls[0]["base"] == "(empty)"

    # the capped-out older statement comes next, on top of the first overview
    generate.run_generation([anchor], corpus, prose, llm, model="m", today="2026-10-16")
    assert llm.calls[1]["base"] == "Definition from 1 statements."
    assert [s["date"] for s in llm.calls[1]["statements"]] == ["2025-01-01"]

    # nothing left: no call
    result = generate.run_generation([anchor], corpus, prose, llm, model="m")
    assert result["generated"] == 0
    assert len(llm.calls) == 2


def test_a_new_version_regenerates_from_scratch() -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    llm = FakeLLM()
    anchor = corpus.entities["anchor-step"]
    generate.run_generation([anchor], corpus, prose, llm, model="m")

    with patch.object(generate, "GENERATOR_VERSION", "next"):
        generate.run_generation([anchor], corpus, prose, llm, model="m")

    assert llm.calls[1]["base"] == "(empty)"
    assert len(llm.calls[1]["statements"]) == 2


# ── notes (MINI-92) ──


def _with_note() -> tuple[WcsWikiExport, generate.Corpus]:
    export = _export()
    note = _source("Note", dt.date(2026, 10, 19)).model_copy(
        update={"session_type": "correction"}
    )
    export = export.model_copy(
        update={
            "sources": [*export.sources, note],
            "attributions": [
                *export.attributions,
                WcsAttribution(
                    id=_id("a-note"),
                    source_id=note.id,
                    entity_id=_id("anchor-step"),
                    attribution_kind="taught",
                    prose="The anchor is the end of the pattern, not a travel step.",
                ),
            ],
        }
    )
    return export, generate.build_corpus(export)


def test_a_note_is_shown_to_the_model_as_one() -> None:
    _, corpus = _with_note()
    by_text = {e.text: e for e in corpus.own["anchor-step"]}
    note = by_text["The anchor is the end of the pattern, not a travel step."]
    lesson = by_text["The anchor travels backward."]
    assert note.as_prompt()["from"] == "note"
    assert "from" not in lesson.as_prompt()


def test_a_new_note_rule_regenerates_only_pages_a_note_speaks_to() -> None:
    _, corpus = _with_note()
    prose = generate.ProseFile()
    llm = FakeLLM()
    anchor, stretch = corpus.entities["anchor-step"], corpus.entities["stretch"]
    generate.run_generation([anchor, stretch], corpus, prose, llm, model="m")
    assert not generate.pending(anchor, corpus, prose)

    with patch.object(generate, "NOTE_RULE_VERSION", "next"):
        _, corpus = _with_note()
        assert [e.text for e in generate.pending(anchor, corpus, prose)] == [
            "The anchor is the end of the pattern, not a travel step."
        ]
        assert not generate.pending(corpus.entities["stretch"], corpus, prose)
        generate.run_generation(
            [corpus.entities["anchor-step"]], corpus, prose, llm, model="m"
        )

    # integrated on top of the current text, not from scratch
    assert llm.calls[-1]["base"] == "Definition from 3 statements."
    assert len(llm.calls[-1]["statements"]) == 1


def test_the_prompt_puts_a_note_ahead_of_the_current_definition() -> None:
    assert '"from": "note"' in generate.SYSTEM_PROMPT
    assert "even when the current definition reads well" in generate.SYSTEM_PROMPT
    assert "never how the word is used" in generate.SYSTEM_PROMPT


# ── the page is the base ──


def _write_page(wiki: Path, rel: str, overview: str, context: str = "") -> None:
    page = md.Page(frontmatter={"slug": rel})
    page.set_section("Overview", [overview])
    if context:
        page.set_section("In practice", [context])
    (wiki / rel).parent.mkdir(parents=True, exist_ok=True)
    (wiki / rel).write_text(md.serialize(page))


def test_an_overview_edited_on_the_page_becomes_the_base(tmp_path: Path) -> None:
    export = _export()
    corpus = generate.build_corpus(export)
    prose = generate.ProseFile()
    llm = FakeLLM()
    anchor = corpus.entities["anchor-step"]
    _write_page(
        tmp_path, "techniques/anchor-step.md", "Hand-written: the anchor is an arrival."
    )

    generate.run_generation(
        [anchor], corpus, prose, llm, model="m", wiki_repo_path=tmp_path
    )

    assert llm.calls[0]["base"] == "Hand-written: the anchor is an arrival."
    bys = [r["by"] for r in prose.entities["anchor-step"]["revisions"]]
    assert bys == ["page", "llm"]


def test_a_page_showing_our_own_older_overview_is_not_an_edit(tmp_path: Path) -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    llm = FakeLLM()
    anchor = corpus.entities["anchor-step"]
    with patch.object(generate, "MAX_OWN_STATEMENTS", 1):
        generate.run_generation([anchor], corpus, prose, llm, model="m")
    # the page was rendered from that first overview, then generation ran again
    _write_page(
        tmp_path,
        "techniques/anchor-step.md",
        prose.definition("anchor-step"),
        prose.context("anchor-step"),
    )

    generate.run_generation(
        [anchor], corpus, prose, llm, model="m", wiki_repo_path=tmp_path
    )

    assert [r["by"] for r in prose.entities["anchor-step"]["revisions"]] == [
        "llm",
        "llm",
    ]


# ── choosing entities ──


def test_named_entities_and_unknown_slugs() -> None:
    _, corpus = _corpus_and_overlay()
    chosen = generate.choose_entities(
        corpus, generate.ProseFile(), slugs=["connection"]
    )
    assert [e.slug for e in chosen] == ["connection"]
    with pytest.raises(RuntimeError, match="nope"):
        generate.choose_entities(corpus, generate.ProseFile(), slugs=["nope"])


def test_a_sample_is_reproducible_and_only_pending() -> None:
    _, corpus = _corpus_and_overlay()
    a = generate.choose_entities(corpus, generate.ProseFile(), sample=2, seed=3)
    b = generate.choose_entities(corpus, generate.ProseFile(), sample=2, seed=3)
    assert [e.slug for e in a] == [e.slug for e in b]
    assert len(a) == 2


# ── render ──


def test_generated_prose_renders_as_the_overview() -> None:
    augmented, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    generate.run_generation(
        [corpus.entities["connection"]], corpus, prose, FakeLLM(), model="m"
    )

    bundle, _ = render_bundle(
        generate.apply_prose(augmented, prose), rendered_at=dt.date(2026, 10, 16)
    )

    page = md.parse(bundle["concepts/connection.md"])
    assert page.get_section("Overview") == ["Definition from 1 statements."]
    assert page.get_section("In practice") is None  # no context passed


def test_generate_local_end_to_end(tmp_path: Path) -> None:
    export = _export()
    links_path = tmp_path / "links.json"
    _links(export).save(links_path)
    prose_path = tmp_path / "prose.json"

    with (
        patch.object(flow, "_fetch_export", return_value=export),
        patch.object(link, "llm_from_env", return_value=(FakeLLM(), "fake/m")),
    ):
        summary = flow.generate_local(
            prose_path, links_path=links_path, entities=["connection"]
        )

    assert summary["generated"] == 1
    assert summary["used_links"] is True
    assert generate.ProseFile.load(prose_path).definition("connection")
    assert (tmp_path / "prose-review.md").exists()


def test_generate_mode_passes_its_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with patch.object(main, "generate_local", return_value={"generated": 0}) as run:
        assert (
            main.main(["generate", "--entity", "connection", "--entity", "anchor"]) == 0
        )
    kwargs = run.call_args.kwargs
    assert kwargs["entities"] == ["connection", "anchor"]
    assert kwargs["wiki_repo_path"] == tmp_path.resolve()
    assert kwargs["links_path"].name == "links.json"


def test_a_failing_entity_is_retried_then_skipped_and_left_pending() -> None:
    from mini_app_polis.llm.errors import LLMValidationError

    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()

    class Flaky(FakeLLM):
        def generate_json(self, **kwargs: Any) -> LLMResult:
            user = kwargs["messages"][-1].content
            if "slug: anchor-step" in user:
                raise LLMValidationError("bad json")
            return super().generate_json(**kwargs)

    entities = [corpus.entities["anchor-step"], corpus.entities["stretch"]]
    result = generate.run_generation(entities, corpus, prose, Flaky(), model="m")

    assert result["failed_slugs"] == ["anchor-step"]
    assert result["generated"] == 1
    assert prose.definition("anchor-step") == ""
    assert prose.integrated("anchor-step") == set()
    assert prose.definition("stretch")


def test_the_prompt_forbids_straight_double_quotes_in_the_text() -> None:
    assert "never use the straight double-quote character" in generate.SYSTEM_PROMPT


def test_definition_comes_first_and_context_renders_under_in_practice() -> None:
    augmented, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    generate.run_generation(
        [corpus.entities["connection"]], corpus, prose, FakeLLM(), model="m"
    )
    export = generate.apply_prose(augmented, prose)

    bundle, _ = render_bundle(
        export,
        rendered_at=dt.date(2026, 10, 16),
        context=generate.context_by_entity(export, prose),
    )

    page = md.parse(bundle["concepts/connection.md"])
    headings = [h for h, _ in page.sections]
    assert headings[:2] == ["Overview", "In practice"]
    assert page.get_section("In practice") == ["Context from 1."]


def test_the_prompt_asks_for_a_short_definition_and_separate_context() -> None:
    assert "at most about 40 words" in generate.SYSTEM_PROMPT.replace("\n", " ")
    assert generate.RESPONSE_SCHEMA["required"] == ["definition", "context"]
    flat = generate.SYSTEM_PROMPT.replace("\\\n", " ").replace("\n", " ")
    assert "See also" in flat


def test_parallel_generation_matches_sequential() -> None:
    _, corpus = _corpus_and_overlay()
    entities = sorted(corpus.entities.values(), key=lambda e: e.slug)
    sequential, parallel = generate.ProseFile(), generate.ProseFile()
    saves: list[int] = []

    generate.run_generation(
        entities, corpus, sequential, FakeLLM(), model="m", today="d"
    )
    result = generate.run_generation(
        entities,
        corpus,
        parallel,
        FakeLLM(),
        model="m",
        today="d",
        workers=4,
        on_entity=lambda: saves.append(1),
    )

    assert result["generated"] == len(entities)
    assert len(saves) == len(entities)
    for e in entities:
        assert parallel.definition(e.slug) == sequential.definition(e.slug)
        assert parallel.integrated(e.slug) == sequential.integrated(e.slug)


def test_all_pending_mentions_are_covered_but_own_statements_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    augmented, corpus = _corpus_and_overlay()
    monkeypatch.setattr(generate, "MAX_OWN_STATEMENTS", 1)
    monkeypatch.setattr(generate, "MAX_REFERRING_STATEMENTS", 0)
    prose = generate.ProseFile()
    llm = FakeLLM()

    # stretch: one own statement (a definition) and one mention; the mention
    # is not sent (cap 0) but is covered by the call.
    generate.run_generation([corpus.entities["stretch"]], corpus, prose, llm, model="m")
    sent = [s["id"] for s in llm.calls[0]["statements"]]
    integrated = prose.integrated("stretch")
    mention = corpus.referring["stretch"][0].key
    assert mention not in sent
    assert mention in integrated

    # anchor-step: two own statements, cap 1 — the second waits for next run
    generate.run_generation(
        [corpus.entities["anchor-step"]], corpus, prose, llm, model="m"
    )
    assert len(prose.integrated("anchor-step")) == 1
    assert generate.pending(corpus.entities["anchor-step"], corpus, prose)


# ── links the model writes ──


def test_links_are_kept_corrected_or_unlinked() -> None:
    augmented, _ = _corpus_and_overlay()
    pages = generate.PageIndex.from_export(augmented)
    source = next(p for p in pages.paths if p.startswith("sources/"))
    text = (
        f"Right [[techniques/anchor-step]], wrong dir [[patterns/anchor-step|the anchor]], "
        f"with anchor [[concepts/stretch#Overview]], source [[{source}]], "
        f"unknown [[techniques/no-such-thing]] and [[nowhere|Label]]."
    )

    fixed = pages.repair(text)

    assert "[[techniques/anchor-step]]" in fixed
    assert "[[techniques/anchor-step|the anchor]]" in fixed
    assert "[[concepts/stretch#Overview]]" in fixed
    assert f"[[{source}]]" in fixed
    assert "unknown no such thing and Label." in fixed


def test_rendered_prose_has_no_broken_links_even_when_stored_with_bad_ones() -> None:
    from wiki_curator_cog.render import find_broken_links

    augmented, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    prose.entities["connection"] = {
        "generator_version": generate.GENERATOR_VERSION,
        "definition": "Links [[techniques/stretch]] and [[techniques/ghost]].",
        "context": "See [[patterns/anchor-step]].",
        "integrated": [],
        "revisions": [],
    }
    export = generate.apply_prose(augmented, prose)
    bundle, _ = render_bundle(
        export,
        rendered_at=dt.date(2026, 10, 16),
        context=generate.context_by_entity(export, prose),
    )

    assert find_broken_links(bundle) == []
    page = bundle["concepts/connection.md"]
    assert "[[concepts/stretch]]" in page and "ghost" in page
    assert "[[techniques/anchor-step]]" in page


def test_a_page_showing_repaired_links_is_not_mistaken_for_an_edit(
    tmp_path: Path,
) -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    prose.entities["anchor-step"] = {
        "generator_version": generate.GENERATOR_VERSION,
        "definition": "See [[concepts/anchor-step]].",
        "context": "",
        "integrated": [],
        "revisions": [
            {"by": "llm", "definition": "See [[concepts/anchor-step]].", "context": ""}
        ],
    }
    assert corpus.pages is not None
    _write_page(
        tmp_path,
        "techniques/anchor-step.md",
        corpus.pages.repair("See [[concepts/anchor-step]]."),
    )

    generate.run_generation(
        [corpus.entities["anchor-step"]],
        corpus,
        prose,
        FakeLLM(),
        model="m",
        wiki_repo_path=tmp_path,
    )

    assert [r["by"] for r in prose.entities["anchor-step"]["revisions"]] == [
        "llm",
        "llm",
    ]


# ── the fixed shape ──


def test_over_shape_counts_words_and_sentences_not_links() -> None:
    short = (
        "It works like this. People get it wrong. It connects to [[concepts/frame]]."
    )
    assert not generate.over_shape("A stop.", short)
    assert generate.over_shape("A stop.", short + " A fourth sentence.")
    assert generate.over_shape("word " * 60, "")
    # a citation does not count as a sentence
    assert not generate.over_shape("A stop.", "One point ([[sources/x]]). Two. Three.")


def test_a_sentence_packing_several_ideas_is_over_shape() -> None:
    """MINI-92: three sentences, but one strings ideas together."""
    packed = (
        "Connection lives in the back body — lats, rear delts, triceps — not the "
        "biceps or pecs; the lats tie the arms to the core, and without them the "
        "frame connects front to front. It matters. It connects."
    )
    assert generate.over_shape("A stop.", packed)
    twenty = "Word " + " ".join(["word"] * 19) + "."
    assert not generate.over_shape("A stop.", f"{twenty} {twenty} {twenty}")
    # a link reads as its label and a citation not at all
    linked = " ".join(["word"] * 28) + " [[concepts/frame|the frame]] ([[sources/x]])."
    assert not generate.over_shape("A stop.", linked)


def test_condense_can_be_tried_on_named_pages_first() -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    for slug in ("anchor-step", "stretch"):
        prose.entities[slug] = {
            "generator_version": generate.GENERATOR_VERSION,
            "definition": "Def.",
            "context": " ".join(f"S{i}." for i in range(8)),
            "integrated": [],
            "revisions": [],
        }
    result = generate.run_condense(
        corpus, prose, LongThenShortLLM(), model="m", only=["stretch"]
    )
    assert result["slugs"] == ["stretch"]
    assert prose.context("anchor-step").startswith("S0.")


def test_the_prompts_ask_for_one_idea_per_sentence() -> None:
    for prompt in (generate.SYSTEM_PROMPT, generate.CONDENSE_PROMPT):
        flat = " ".join(prompt.replace("\\\n", " ").split())
        assert "Never join two points in one sentence" in flat
        assert "Never use a semicolon, a long dash" in flat
        # The model copies the instructions' own style, so they use neither.
        assert "—" not in prompt
        assert ";" not in prompt
    flat = " ".join(generate.CONDENSE_PROMPT.replace("\\\n", " ").split())
    assert "Reword, never reinterpret" in flat


def test_word_talk_is_removed_from_a_definition() -> None:
    strike = (
        "The action of the foot pressing into the floor. "
        "'Foot strike' is another name for it, but 'strike' is the preferred term."
    )
    assert generate.without_word_talk(strike) == (
        "The action of the foot pressing into the floor."
    )
    assert generate.without_word_talk("Only the thing.") == "Only the thing."


class RewritesEverythingLLM(FakeLLM):
    """A condense answer that also rewrites the definition, as strike's did."""

    def __init__(self, context: str) -> None:
        super().__init__()
        self.context = context

    def generate_json(
        self, *, messages, json_schema, schema_name="output"
    ) -> LLMResult:
        return LLMResult(
            "fake",
            "fake",
            {"definition": "The moment the foot lands.", "context": self.context},
            "",
        )


def _one_over_shape(definition: str) -> tuple[generate.Corpus, generate.ProseFile]:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    prose.entities["anchor-step"] = {
        "generator_version": generate.GENERATOR_VERSION,
        "definition": definition,
        "context": " ".join(f"S{i}." for i in range(8)),
        "integrated": [],
        "revisions": [],
    }
    return corpus, prose


def test_condense_keeps_a_definition_within_its_shape() -> None:
    """MINI-92: the rewrite undid strike's note-led definition."""
    corpus, prose = _one_over_shape(
        "The foot pressing into the floor. 'Foot strike' is another name for it."
    )
    generate.run_condense(
        corpus, prose, RewritesEverythingLLM("One point. Another."), model="m"
    )
    assert prose.definition("anchor-step") == "The foot pressing into the floor."
    assert prose.context("anchor-step") == "One point. Another."


def test_a_dry_run_condense_judges_nothing() -> None:
    """The dry-run client answers nothing, so every page would look unchanged."""
    corpus, prose = _one_over_shape("Def.")
    result = generate.run_condense(
        corpus, prose, RewritesEverythingLLM("Still. " * 5), model="m", dry_run=True
    )
    assert result["not_kept"] == 0
    assert result["slugs"] == ["anchor-step"]


@pytest.mark.parametrize(
    "context",
    [
        "One point — joined to another. Two.",
        "Still. " * 5,
    ],
)
def test_a_rewrite_still_over_the_shape_is_not_stored(context: str) -> None:
    corpus, prose = _one_over_shape("Def.")
    before = prose.context("anchor-step")
    result = generate.run_condense(
        corpus, prose, RewritesEverythingLLM(context.strip()), model="m"
    )
    assert result["not_kept_slugs"] == ["anchor-step"]
    assert result["not_kept_why"]["anchor-step"]["problems"]
    assert result["slugs"] == []
    assert prose.context("anchor-step") == before


class FixesOnSecondTryLLM(FakeLLM):
    """Joins two points with a long dash first; told what is wrong, fixes it."""

    def generate_json(
        self, *, messages, json_schema, schema_name="output"
    ) -> LLMResult:
        user = messages[-1].content
        self.calls.append({"user": user})
        context = (
            "Short point. Another short point."
            if "not accepted because the context uses a long dash" in user
            else "One point — another point."
        )
        return LLMResult("fake", "fake", {"definition": "D.", "context": context}, "")


def test_a_rejected_rewrite_gets_one_more_try_told_what_to_fix() -> None:
    corpus, prose = _one_over_shape("Def.")
    llm = FixesOnSecondTryLLM()
    result = generate.run_condense(corpus, prose, llm, model="m")
    assert len(llm.calls) == 2
    assert "One point — another point." in llm.calls[1]["user"]
    assert result["slugs"] == ["anchor-step"]
    assert prose.context("anchor-step") == "Short point. Another short point."


def test_a_citation_is_kept_once_before_the_full_stop() -> None:
    """The sugar push rewrite cited one source after every sentence."""
    src = "([[sources/2026-09-23-level-3-week-4]])"
    messy = f"First point. {src} Second point. {src} Third point. {src}"
    assert (
        generate.tidy_citations(messy)
        == f"First point. Second point. Third point {src}."
    )
    two = "One ([[sources/a]], [[sources/b]]). Two ([[sources/c]])."
    assert generate.tidy_citations(two) == two


def test_the_review_shows_what_a_rewrite_replaced() -> None:
    corpus, prose = _one_over_shape("Def.")
    before = prose.context("anchor-step")
    entry = prose.entities["anchor-step"]
    entry["revisions"] = [{"by": "llm", "definition": "Def.", "context": before}]
    result = generate.run_condense(
        corpus, prose, RewritesEverythingLLM("One point. Another."), model="m"
    )
    review = generate.review_markdown(prose, result["slugs"])
    assert "**In practice.** One point. Another." in review
    assert f"_In practice before:_ {before}" in review
    assert "_Definition before:_" not in review


def test_a_single_semicolon_is_not_refused() -> None:
    context = "The common mistake is rushing; instead, wait for the lead. Two. Three."
    assert generate.shape_problems("D.", context) == []


def test_shape_problems_say_what_to_fix() -> None:
    long = "Word " + " ".join(["word"] * 34) + "."
    assert generate.shape_problems("D.", f"{long} Two; three — four.") == [
        "a context sentence has 35 words (at most 30)",
        "the context uses a long dash",
    ]
    assert generate.shape_problems("D.", "Fine. Short.") == []


class LongThenShortLLM(FakeLLM):
    """The first answer is over shape; a condense call returns a short one."""

    def generate_json(
        self, *, messages, json_schema, schema_name="output"
    ) -> LLMResult:
        if messages[0].content == generate.CONDENSE_PROMPT:
            self.calls.append({"condense": True})
            return LLMResult(
                "fake", "fake", {"definition": "Short.", "context": "One. Two."}, ""
            )
        self.calls.append({"condense": False})
        long = " ".join(f"Sentence {i}." for i in range(6))
        return LLMResult("fake", "fake", {"definition": "Short.", "context": long}, "")


def test_an_over_shape_answer_is_condensed_before_it_is_stored() -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    llm = LongThenShortLLM()

    generate.run_generation(
        [corpus.entities["anchor-step"]], corpus, prose, llm, model="m"
    )

    assert [c["condense"] for c in llm.calls] == [False, True]
    assert prose.context("anchor-step") == "One. Two."


def test_condense_rewrites_only_pages_over_shape_and_keeps_the_watermark() -> None:
    _, corpus = _corpus_and_overlay()
    prose = generate.ProseFile()
    for slug, context in [
        ("anchor-step", " ".join(f"S{i}." for i in range(8))),
        ("stretch", "Fine."),
    ]:
        prose.entities[slug] = {
            "generator_version": generate.GENERATOR_VERSION,
            "definition": "Def.",
            "context": context,
            "integrated": ["k1"],
            "revisions": [],
        }
    llm = LongThenShortLLM()

    result = generate.run_condense(corpus, prose, llm, model="m")

    assert result["slugs"] == ["anchor-step"]
    assert prose.context("anchor-step") == "One. Two."
    assert prose.context("stretch") == "Fine."
    assert prose.integrated("anchor-step") == {"k1"}
    assert prose.entities["anchor-step"]["revisions"][-1]["statements"] == 0


def test_the_prompt_fixes_the_shape_and_rewrites_rather_than_appends() -> None:
    flat = " ".join(generate.SYSTEM_PROMPT.replace("\\\n", " ").split())
    assert "at most three sentences" in flat
    assert "It never grows" in flat
    assert "a draft to improve" in flat


def test_generate_mode_passes_condense(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with patch.object(main, "generate_local", return_value={}) as run:
        assert main.main(["generate", "--condense", "--workers", "8"]) == 0
    assert run.call_args.kwargs["condense"] is True
    assert run.call_args.kwargs["workers"] == 8


# ── a local run rebuilds the pages it changed ──


@pytest.mark.parametrize(
    ("argv", "result", "rebuilt"),
    [
        (["generate"], {"generated_slugs": ["strike", "timing"]}, ["strike", "timing"]),
        (["generate"], {"generated_slugs": []}, None),
        (["generate", "--dry-run"], {"generated_slugs": ["strike"]}, None),
        (["generate", "--state", "files"], {"generated_slugs": ["strike"]}, None),
        (
            ["generate", "--condense"],
            {"slugs": ["connection"], "generated_slugs": ["x"]},
            ["connection"],
        ),
    ],
)
def test_a_local_generate_rebuilds_the_pages_it_changed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    result: dict,
    rebuilt: list[str] | None,
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with (
        patch.object(main, "api_state", return_value="api"),
        patch.object(main, "generate_local", return_value=dict(result)),
        patch.object(main, "materialise_local", return_value={"pages": 2}) as mat,
    ):
        assert main.main(argv) == 0
    if rebuilt is None:
        mat.assert_not_called()
    else:
        mat.assert_called_once_with(
            tmp_path / ".curator", entities=rebuilt, state="api"
        )


@pytest.mark.parametrize(
    ("command", "runner", "result", "every_page"),
    [
        ("link", "link_local", {"linked": 3}, True),
        ("link", "link_local", {"linked": 0}, False),
        ("consolidate", "consolidate_local", {"decided": 5}, True),
        ("consolidate", "consolidate_local", {"decided": 0}, False),
    ],
)
def test_a_local_link_or_consolidate_rebuilds_every_page_when_it_changed_any(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    runner: str,
    result: dict,
    every_page: bool,
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with (
        patch.object(main, "api_state", return_value="api"),
        patch.object(main, runner, return_value=dict(result)),
        patch.object(main, "materialise_local", return_value={}) as mat,
    ):
        assert main.main([command]) == 0
    if every_page:
        mat.assert_called_once_with(tmp_path / ".curator", entities=None, state="api")
    else:
        mat.assert_not_called()


# ── putting back a rewrite that changed meaning (MINI-92) ──


def _rewritten(prose: generate.ProseFile, slug: str, *, statements: int = 0) -> None:
    prose.entities[slug] = {
        "generator_version": generate.GENERATOR_VERSION,
        "definition": "Def.",
        "context": "Rewritten, wrongly.",
        "integrated": ["k1"],
        "revisions": [
            {
                "by": "llm",
                "statements": 3,
                "definition": "Def.",
                "context": "Accurate.",
            },
            {
                "by": "llm",
                "statements": statements,
                "definition": "Def.",
                "context": "Rewritten, wrongly.",
            },
        ],
    }


def test_revert_puts_back_the_text_before_the_rewrite() -> None:
    prose = generate.ProseFile()
    _rewritten(prose, "tuck-turn")
    result = generate.revert(prose, ["tuck-turn"], today="2026-10-19")
    assert result["slugs"] == ["tuck-turn"]
    assert prose.context("tuck-turn") == "Accurate."
    assert prose.integrated("tuck-turn") == {"k1"}
    assert prose.entities["tuck-turn"]["revisions"][-1]["by"] == "page"
    # twice is a no-op, not a step further back
    again = generate.revert(prose, ["tuck-turn"])
    assert again["refused"] == {"tuck-turn": "its text was last chosen by a person"}
    assert prose.context("tuck-turn") == "Accurate."


def test_revert_refuses_a_page_whose_last_change_was_new_evidence() -> None:
    prose = generate.ProseFile()
    _rewritten(prose, "strike", statements=2)
    result = generate.revert(prose, ["strike", "nowhere"])
    assert result["slugs"] == []
    assert set(result["refused"]) == {"strike", "nowhere"}
    assert prose.context("strike") == "Rewritten, wrongly."


def test_a_rewrite_pass_leaves_a_reverted_page_alone() -> None:
    corpus, prose = _one_over_shape("Def.")
    entry = prose.entities["anchor-step"]
    entry["revisions"] = [{"by": "page", "definition": "Def.", "context": "x"}]
    result = generate.run_condense(
        corpus, prose, RewritesEverythingLLM("One. Two."), model="m"
    )
    assert result["over_shape"] == 0


def test_placeholder_citations_are_removed() -> None:
    text = "One point [(source)]. Two [citation needed]. Three [citation]."
    assert generate.tidy_citations(text) == "One point. Two. Three."


def test_generate_revert_passes_through_and_rebuilds_what_it_reverted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with (
        patch.object(main, "api_state", return_value="api"),
        patch.object(
            main, "generate_local", return_value={"slugs": ["tuck-turn"]}
        ) as run,
        patch.object(main, "materialise_local", return_value={}) as mat,
    ):
        assert main.main(["generate", "--revert", "--entity", "tuck-turn"]) == 0
    assert run.call_args.kwargs["revert"] is True
    mat.assert_called_once_with(
        tmp_path / ".curator", entities=["tuck-turn"], state="api"
    )


def test_revert_needs_named_pages(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="needs --entity"):
        with patch.object(flow, "_fetch_export", return_value=_export()):
            flow.generate_local(tmp_path / "prose.json", revert=True)


def test_a_revert_is_a_revision_the_api_accepts() -> None:
    """The API's contract knows only llm and page (the first revert run failed)."""
    from mini_app_polis.api.contract import WcsWikiProseRevision

    prose = generate.ProseFile()
    _rewritten(prose, "tuck-turn")
    generate.revert(prose, ["tuck-turn"], today="2026-10-19")
    WcsWikiProseRevision.model_validate(prose.entities["tuck-turn"]["revisions"][-1])
