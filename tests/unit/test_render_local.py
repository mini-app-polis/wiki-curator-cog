"""The local proof-of-concept loop: link check, corpus quality, render mode.

ADR-005 / MINI-83. A local render writes into an existing clone and stops:
no clone, commit, push or run report.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

import wiki_curator_cog.flow as flow
import wiki_curator_cog.main as main
from wiki_curator_cog.render import (
    build_indexes,
    corpus_quality,
    find_broken_links,
    render_bundle,
)

from .export_builders import (
    WcsAttribution,
    WcsDefinition,
    WcsEntity,
    WcsRelation,
    WcsSource,
    WcsWikiExport,
)


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


def _source(name: str, month: int):
    return WcsSource(
        id=_id(f"s-{name}"),
        transcript_id=_id(f"t-{name}"),
        title=name,
        session_date=dt.date(2026, month, 1),
        session_type="private_lesson",
        visibility="private",
        is_default_visible=False,
        created_at=dt.datetime(2026, month, 1, tzinfo=dt.UTC),
    )


def _export() -> WcsWikiExport:
    """Three concepts: one taught in two sources, one in one, one only related."""
    anchor = WcsEntity(
        id=_id("anchor"), slug="anchor", canonical_name="Anchor", kind="concept"
    )
    stretch = WcsEntity(
        id=_id("stretch"), slug="stretch", canonical_name="Stretch", kind="concept"
    )
    frame = WcsEntity(
        id=_id("frame"), slug="frame", canonical_name="Frame", kind="concept"
    )
    one = _source("One", 1)
    two = _source("Two", 2)
    return WcsWikiExport(
        entities=[anchor, stretch, frame],
        sources=[one, two],
        attributions=[
            WcsAttribution(
                id=_id("a1"),
                source_id=one.id,
                entity_id=anchor.id,
                attribution_kind="taught",
            ),
            WcsAttribution(
                id=_id("a2"),
                source_id=two.id,
                entity_id=anchor.id,
                attribution_kind="taught",
            ),
        ],
        definitions=[
            WcsDefinition(
                id=_id("d1"), source_id=one.id, entity_id=stretch.id, term="Stretch"
            ),
        ],
        relations=[
            WcsRelation(
                id=_id("r1"),
                from_entity_id=anchor.id,
                to_entity_id=frame.id,
                relation_kind="depends-on",
            )
        ],
    )


# ── find_broken_links ──


def test_a_rendered_bundle_has_no_broken_links() -> None:
    bundle, _ = render_bundle(_export(), rendered_at=dt.date(2026, 10, 16))
    assert find_broken_links(bundle) == []


def test_a_link_to_a_page_nobody_renders_is_reported() -> None:
    bundle = {
        "concepts/anchor.md": "See [[concepts/frame]] and [[concepts/stretch|stretching]].",
        "concepts/stretch.md": "",
    }
    assert find_broken_links(bundle) == [("concepts/anchor.md", "concepts/frame")]


def test_labels_headings_and_md_suffixes_resolve_to_the_page() -> None:
    bundle = {
        "a.md": "[[b|label]] [[b#Heading]] [[b.md]]",
        "b.md": "",
    }
    assert find_broken_links(bundle) == []


def test_pages_already_in_the_repo_count_as_targets() -> None:
    bundle = {"index.md": "Read [[CLAUDE]] first."}
    assert find_broken_links(bundle) == [("index.md", "CLAUDE")]
    assert find_broken_links(bundle, existing_paths={"CLAUDE.md"}) == []


# ── corpus_quality ──


def test_corpus_quality_separates_thin_single_source_and_isolated() -> None:
    export = _export()
    quality = corpus_quality(export, build_indexes(export))

    assert quality["entities_by_kind"] == {"concept": 3}
    # frame exists only as the far end of a relation: a page, but nothing said.
    assert quality["thin"] == {"count": 1, "slugs": ["frame"]}
    # stretch is defined in one source; anchor is taught in two.
    assert quality["single_source"] == {"count": 1, "slugs": ["stretch"]}
    # stretch has no relation in either direction; anchor and frame share one.
    assert quality["isolated"] == {"count": 1, "slugs": ["stretch"]}


# ── render_local ──


def _clone(tmp_path: Path) -> Path:
    wiki = tmp_path / "wcs-wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "CLAUDE.md").write_text("# spec\n")
    (wiki / "log.md").write_text("# log\n")
    (wiki / "concepts" / "gone.md").write_text("no longer produced\n")
    return wiki


def test_render_local_writes_pages_removes_stale_and_leaves_the_log(
    tmp_path: Path,
) -> None:
    wiki = _clone(tmp_path)
    with patch.object(flow, "_fetch_export", return_value=_export()):
        summary = flow.render_local(wiki)

    assert (wiki / "concepts" / "frame.md").exists()
    assert not (wiki / "concepts" / "gone.md").exists()
    assert summary["pages_removed"] == ["concepts/gone.md"]
    assert (wiki / "log.md").read_text() == "# log\n"
    assert summary["broken_links"] == {"count": 0, "links": []}
    assert summary["quality"]["thin"]["slugs"] == ["frame"]


@pytest.mark.parametrize(
    "export",
    [
        WcsWikiExport(),
        WcsWikiExport(entities=_export().entities),
        WcsWikiExport(sources=_export().sources),
    ],
    ids=["empty", "no-sources", "no-entities"],
)
def test_an_empty_export_writes_and_removes_nothing(
    tmp_path: Path, export: WcsWikiExport
) -> None:
    wiki = _clone(tmp_path)
    with (
        patch.object(flow, "_fetch_export", return_value=export),
        pytest.raises(flow.EmptyExportError),
    ):
        flow.render_local(wiki)
    assert (wiki / "concepts" / "gone.md").exists()
    assert sorted(p.name for p in wiki.rglob("*.md")) == [
        "CLAUDE.md",
        "gone.md",
        "log.md",
    ]


def test_render_local_refuses_a_directory_that_is_not_the_wiki(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="CLAUDE.md"):
        flow.render_local(tmp_path)


# ── main render mode ──


def _summary(broken: int) -> dict:
    return {"broken_links": {"count": broken, "links": []}}


def test_render_mode_renders_into_the_wiki_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with patch.object(main, "render_local", return_value=_summary(0)) as render:
        assert main.main(["render", "--state", "files"]) == 0
    render.assert_called_once_with(tmp_path.resolve(), None, None, None)
    assert json.loads(capsys.readouterr().out) == _summary(0)


def test_render_mode_exits_non_zero_on_a_broken_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with patch.object(main, "render_local", return_value=_summary(2)):
        assert main.main(["render"]) == 1


def test_render_mode_needs_a_wiki_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIKI_REPO_PATH", raising=False)
    with patch.object(main, "render_local", side_effect=AssertionError("no path")):
        assert main.main(["render"]) == 2
