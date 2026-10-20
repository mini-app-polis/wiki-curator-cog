"""Page documents (ADR-006): built once, stored by the API, exported as markdown.

The document is the page model; the markdown export and the API both read
it. These pin the parts a reader depends on: neutral links that come back to
the exact repo paths, citations that carry their source, evidence that names
every source the prose was built from, and a materialise job that stores
pages, removes the ones folded away and asks for nothing after.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import wiki_curator_cog.flow as flow
import wiki_curator_cog.main as main
from wiki_curator_cog import consolidate, pages, state_backend, units
from wiki_curator_cog.render import build_indexes, render_bundle

from .test_link import _export, _id
from .test_state_store import FakeApi
from .test_units import RouterLLM, _ctx

SOURCE = _id("s1")
PATHS = {"stretch": "concepts/stretch", "anchor-step": "techniques/anchor-step"}
SOURCES = {"2026-03-01-one": SOURCE}


@pytest.mark.parametrize(
    "text",
    [
        "Keep [[concepts/stretch]] in [[techniques/anchor-step|the anchor]].",
        "See [[concepts/stretch#Overview|stretch]] and [[sources/2026-03-01-one]].",
        "As taught in [[sources/2026-03-01-one|one lesson]].",
        # not a page this wiki has, or not one at that path: left alone
        "[[concepts/gone]], [[patterns/stretch]], [[instructors/kaiano]], [[x]].",
        "[[sources/unknown]] and [[concepts/stretch|]].",
    ],
)
def test_neutral_links_come_back_as_they_were(text: str) -> None:
    neutral = pages.neutral_links(text, PATHS, SOURCES)
    assert "concepts/stretch]" not in neutral and "sources/2026" not in neutral
    back = pages.path_links(neutral, PATHS, {v: k for k, v in SOURCES.items()})
    assert back == text


def test_links_become_neutral() -> None:
    text = "[[techniques/anchor-step|the anchor]] in [[sources/2026-03-01-one|class]]"
    assert pages.neutral_links(text, PATHS, SOURCES) == (
        f"[[anchor-step|the anchor]] in [[source:{SOURCE}|class]]"
    )


def test_a_document_cites_its_sources() -> None:
    export = _export()
    indexes = build_indexes(export)
    anchor = indexes.entities_by_slug["anchor-step"]
    doc = pages.page_document(anchor, indexes, evidence={SOURCE})
    statement = doc.by_teacher[0].statements[0]
    assert statement.kind == "attribution" and statement.term == "anchor step"
    assert statement.citation.source_id == SOURCE
    # the fixture's source is private
    assert statement.citation.public is False
    assert [c.source_id for c in doc.sources] == [SOURCE]
    assert doc.evidence_sources == [SOURCE]
    assert sorted(r.skill for r in doc.requires) == ["", "Settle over the foot"]


@pytest.mark.parametrize(
    ("name", "aliases", "expected"),
    [
        # The lateral-pushing page as seen on 2026-10-18.
        (
            "lateral pushing",
            [
                "lateral-pushing",
                "lateral push",
                "lateral-push",
                "lateral pushing direction",
                "lateral-pushing-direction",
            ],
            ["lateral push", "lateral pushing direction"],
        ),
        # The page's own name, in any case, is not an alias.
        (
            "Anchor Step",
            ["anchor step", "ANCHOR STEP", "Anchor Footwork"],
            ["Anchor Footwork"],
        ),
        # A slug form with no spelled-out twin is the only form of that name.
        ("Anchor Step", ["anchor-footwork"], ["anchor-footwork"]),
        # Repeats ignoring case go; the first spelling stays.
        ("Sugar Push", ["Push Break", "push break"], ["Push Break"]),
        ("Sugar Push", [], []),
    ],
)
def test_also_called_leaves_out_slug_forms_and_repeats(
    name: str, aliases: list[str], expected: list[str]
) -> None:
    assert pages.display_aliases(name, aliases) == expected


def test_also_called_leaves_out_hidden_names_in_any_form() -> None:
    aliases = ["foot strike", "Toe Strike", "toe-strike", "heel step"]
    assert pages.display_aliases("Strike", aliases, ["toe strike", "heel-step"]) == [
        "foot strike"
    ]


def test_a_document_lists_aliases_once_but_matching_keeps_them_all() -> None:
    export = _export()
    anchor = next(e for e in export.entities if e.slug == "anchor-step")
    anchor.aliases = ["anchor-step", "Anchor Footwork", "anchor-footwork"]
    indexes = build_indexes(export)
    doc = pages.page_document(indexes.entities_by_slug["anchor-step"], indexes)
    assert doc.aliases == ["Anchor Footwork"]
    assert indexes.entities_by_slug["anchor-step"].aliases == [
        "anchor-step",
        "Anchor Footwork",
        "anchor-footwork",
    ]


def test_generated_prose_links_survive_the_export() -> None:
    export = _export()
    slug = build_indexes(export).source_slug_by_id[SOURCE]
    definition = f"Taught in [[sources/{slug}|one]]; see [[techniques/anchor-step]]."
    stretch = export.entities[0].model_copy(update={"overview_md": definition})
    export = export.model_copy(update={"entities": [stretch, *export.entities[1:]]})
    bundle, _ = render_bundle(export, rendered_at=dt.date(2026, 10, 17))
    assert f"## Overview\n{definition}\n" in bundle["concepts/stretch.md"]
    doc = pages.build_documents(export)["stretch"]
    assert doc.definition == (
        f"Taught in [[source:{SOURCE}|one]]; see [[anchor-step]]."
    )


# ── The materialise job ──


def test_materialise_writes_each_page_and_asks_for_nothing(tmp_path: Path) -> None:
    llm = RouterLLM()
    units.handle({"job": "generate", "entity": "stretch"}, _ctx(tmp_path, llm))
    result = units.handle(
        {"job": "materialise", "entities": ["stretch", "gone"]}, _ctx(tmp_path, llm)
    )
    assert result["entities"] == ["stretch"] and result["removed"] == ["gone"]
    assert result["deleted"] == 0  # it never had a page
    assert result["next"] == []
    doc = json.loads((tmp_path / "pages" / "stretch.json").read_text())
    assert doc["definition"] == "A definition."
    # its own definition's source, and no model call to build it
    assert doc["evidence_sources"] == [str(SOURCE)]
    assert llm.calls["prose"] == 1


def test_a_folded_page_goes_when_its_parent_is_materialised(tmp_path: Path) -> None:
    facets = consolidate.FacetsFile()
    facets.record("anchor-step", "stretch", "a detail of stretch", "m")
    facets.save(tmp_path / "facets.json")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "anchor-step.json").write_text("{}")
    result = units.handle(
        {"job": "materialise", "entities": ["anchor-step"]},
        _ctx(tmp_path, RouterLLM()),
    )
    assert result["entities"] == ["stretch"]
    assert result["removed"] == ["anchor-step"] and result["deleted"] == 1
    assert not (tmp_path / "pages" / "anchor-step.json").exists()
    doc = json.loads((tmp_path / "pages" / "stretch.json").read_text())
    assert [d["slug"] for d in doc["details"]] == ["anchor-step"]
    # a lookup of the folded slug can be served this page
    assert doc["folded"] == ["anchor-step"]
    assert doc["requested_slug"] is None


def test_a_same_idea_fold_is_listed_though_not_a_detail(tmp_path: Path) -> None:
    # Same-idea names fold as aliases with no Details line, so ``folded`` is
    # the only place a page names them.
    facets = consolidate.FacetsFile()
    facets.record("anchor-step", "stretch", "the same idea", "m", same=True)
    facets.save(tmp_path / "facets.json")
    units.handle(
        {"job": "materialise", "entities": ["stretch"]}, _ctx(tmp_path, RouterLLM())
    )
    doc = json.loads((tmp_path / "pages" / "stretch.json").read_text())
    assert doc["details"] == []
    assert doc["folded"] == ["anchor-step"]


def test_generate_names_materialise_for_every_page_asked_about(tmp_path: Path) -> None:
    result = units.handle(
        {"job": "generate", "entities": ["stretch", "gone"]},
        _ctx(tmp_path, RouterLLM()),
    )
    assert result["next"] == [{"job": "materialise", "entities": ["gone", "stretch"]}]


def test_a_bad_materialise_job_is_refused(tmp_path: Path) -> None:
    # An empty list named nothing; no list at all is every page.
    with pytest.raises(units.JobError, match="non-empty entities list"):
        units.handle(
            {"job": "materialise", "entities": []}, _ctx(tmp_path, RouterLLM())
        )


def test_materialise_with_no_entities_builds_every_page(tmp_path: Path) -> None:
    # The /admin/wiki rebuild: every page in this one job, with
    # one export read, and nothing to follow.
    llm = RouterLLM()
    result = units.handle({"job": "materialise"}, _ctx(tmp_path, llm))
    every = sorted(e.slug for e in _export().entities)
    assert result["entities"] == every
    assert result["written"] == len(every)
    assert result["next"] == []
    assert sorted(p.stem for p in (tmp_path / "pages").iterdir()) == every
    assert sum(llm.calls.values()) == 0


def test_pages_go_to_the_api_in_chunks_removals_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_backend, "PAGES_PER_POST", 1)
    api = FakeApi()
    api.pages["old"] = {}
    store = state_backend.ApiState(api)
    docs = pages.build_documents(_export(), only={"stretch", "wall-drill"})
    counts = store.save_pages(list(docs.values()), ["old"], tmp_path / "pages")
    assert counts == {
        "written": 2,
        "removed": 1,
        "created": ["stretch", "wall-drill"],
        "changed": [],
        "removed_slugs": ["old"],
    }
    assert [(len(p.pages), p.remove) for p in api.page_posts] == [
        (1, []),
        (1, []),
        (0, ["old"]),
    ]
    assert set(api.pages) == {"stretch", "wall-drill"}
    # The same pages again: written, but nothing changed.
    again = store.save_pages(list(docs.values()), [], tmp_path / "pages")
    assert (again["written"], again["created"], again["changed"]) == (2, [], [])


# ── Facets are read once per job (ride-along) ──


def test_a_link_job_reads_the_facets_once(tmp_path: Path) -> None:
    api = FakeApi()
    units.handle(
        {"job": "link", "source_id": str(SOURCE)},
        _ctx(tmp_path, RouterLLM(), state_backend.ApiState(api)),
    )
    assert api.gets["facets"] == 1


# ── The local command ──


def test_the_materialise_command_builds_every_page_dry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    (tmp_path / ".curator").mkdir()
    with patch.object(flow, "_fetch_export", return_value=_export()):
        code = main.main(["materialise", "--state", "files", "--dry-run"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "dry_run": True,
        "pages": 3,
        "gone": 0,
        "written": 0,
        "deleted": 0,
    }
    assert not (tmp_path / ".curator" / "pages").exists()


def test_evidence_includes_statements_that_refer_to_the_page(tmp_path: Path) -> None:
    # connection has no statements of its own: a proposed concept, referred to
    key = f"attribution:{_id('a1')}"
    llm = RouterLLM(links={key: [{"name": "connection", "polarity": "assert"}]})
    units.handle({"job": "link", "source_id": str(SOURCE)}, _ctx(tmp_path, llm))
    result = units.handle(
        {"job": "materialise", "entities": ["connection"]}, _ctx(tmp_path, llm)
    )
    assert result["entities"] == ["connection"]
    doc = json.loads((tmp_path / "pages" / "connection.json").read_text())
    assert doc["by_teacher"] == []
    assert doc["evidence_sources"] == [str(SOURCE)]
    assert [r["slug"] for r in doc["referenced_by"]] == ["anchor-step"]
