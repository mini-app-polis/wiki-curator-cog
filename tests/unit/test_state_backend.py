"""The API as the curator's state store.

Read from the API, write back what changed, keep the files as a checked
cache — and never let either side silently overwrite the other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mini_app_polis.api.contract import WcsWikiThinEntity

import wiki_curator_cog.flow as flow
import wiki_curator_cog.main as main
from wiki_curator_cog import link, state_backend, state_store

from .test_link import FakeLLM, _ref
from .test_link import _export as _link_export
from .test_state_store import KEY, FakeApi, _write_files

FILES = ("links.json", "facets.json", "prose.json")


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[FakeApi, Path, Path]:
    """An API holding the fixture files, and a separate empty cache folder."""
    src = tmp_path / "src"
    src.mkdir()
    _write_files(src)
    api = FakeApi()
    state_store.import_state(api, src)
    cache = tmp_path / "cache"
    cache.mkdir()
    return api, src, cache


def test_a_read_from_the_api_saves_the_files_byte_for_byte(seeded) -> None:
    api, src, cache = seeded
    store = state_backend.ApiState(api)
    files = state_backend.FileState()
    for name in ("links", "facets", "prose"):
        from_api = getattr(store, name)(cache / f"{name}.json")
        from_file = getattr(files, name)(src / f"{name}.json")
        store.save(from_api, cache / f"{name}.json", final=True)
        files.save(from_file, src / f"{name}.json")
        assert (cache / f"{name}.json").read_bytes() == (
            src / f"{name}.json"
        ).read_bytes()
    assert api.posts == {"links": 1, "facets": 1, "prose": 1}  # the import


def test_an_unchanged_run_sends_nothing(seeded) -> None:
    api, src, _ = seeded
    store = state_backend.ApiState(api)
    links = store.links(src / "links.json")
    store.save(links, src / "links.json", final=True)
    assert api.posts["links"] == 1


def test_a_file_that_disagrees_with_the_api_stops_everything(seeded) -> None:
    api, src, _ = seeded
    raw = json.loads((src / "prose.json").read_text())
    raw["entities"]["anchor"]["definition"] = "Paid for, never sent."
    (src / "prose.json").write_text(json.dumps(raw))
    with pytest.raises(state_backend.StateMismatchError, match="1 changed"):
        state_backend.ApiState(api).prose(src / "prose.json")
    assert json.loads((src / "prose.json").read_text()) == raw  # untouched


def test_only_changed_entries_are_sent_and_only_when_due(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _, cache = seeded
    monkeypatch.setattr(state_backend, "FLUSH_SECONDS", 3600.0)
    store = state_backend.ApiState(api)
    facets = store.facets(cache / "facets.json")
    assert facets is not None
    facets.entities["anchor"]["reason"] = "revised"

    store.save(facets, cache / "facets.json")  # below the batch size: waits
    assert api.posts["facets"] == 1
    store.save(facets, cache / "facets.json", final=True)
    assert api.posts["facets"] == 2
    assert api.docs["facets"]["entities"]["anchor"]["reason"] == "revised"
    assert state_store.verify_state(api, cache, only=("facets",))["ok"] is True


def test_a_full_batch_is_sent_mid_run(seeded, monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, cache = seeded
    monkeypatch.setattr(state_backend, "FLUSH_ENTRIES", 1)
    monkeypatch.setattr(state_backend, "FLUSH_SECONDS", 3600.0)
    store = state_backend.ApiState(api)
    facets = store.facets(cache / "facets.json")
    assert facets is not None
    facets.entities["anchor"]["reason"] = "revised"
    store.save(facets, cache / "facets.json")
    assert api.posts["facets"] == 2


def test_a_refused_write_stops_the_run_with_the_file_written(seeded) -> None:
    api, _, cache = seeded
    store = state_backend.ApiState(api)
    prose = store.prose(cache / "prose.json")
    assert prose is not None
    prose.entities["anchor"]["definition"] = "New."

    def refuse(doc):
        raise RuntimeError("503")

    api.put_wcs_wiki_prose = refuse  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="503"):
        store.save(prose, cache / "prose.json", final=True)
    saved = json.loads((cache / "prose.json").read_text())
    assert saved["entities"]["anchor"]["definition"] == "New."
    # and the next read refuses until the file is imported
    with pytest.raises(state_backend.StateMismatchError):
        state_backend.ApiState(api).prose(cache / "prose.json")


def test_an_unknown_field_is_never_sent(seeded) -> None:
    api, _, cache = seeded
    store = state_backend.ApiState(api)
    links = store.links(cache / "links.json")
    assert links is not None
    links.statements[KEY]["tokens"] = 12
    with pytest.raises(state_store.UnknownFieldsError, match="tokens"):
        store.save(links, cache / "links.json", final=True)
    assert api.posts["links"] == 1


def test_saving_needs_a_read_first(seeded) -> None:
    api, _, cache = seeded
    with pytest.raises(RuntimeError, match="not read from the API"):
        state_backend.ApiState(api).save(link.LinksFile(), cache / "links.json")


def test_a_link_run_writes_its_references_to_the_api(tmp_path: Path) -> None:
    api = FakeApi()
    path = tmp_path / "links.json"
    statements = link.collect_statements(_link_export())
    attribution = next(s for s in statements if s.kind == "attribution")
    llm = FakeLLM({attribution.key: [_ref("connection")]})

    with (
        patch.object(flow, "_fetch_export", return_value=_link_export()),
        patch.object(link, "llm_from_env", return_value=(llm, "fake/model")),
    ):
        summary = flow.link_local(path, state=state_backend.ApiState(api))

    assert summary["linked"] == 4
    stored = api.docs["links"]["statements"]
    assert set(stored) == {s.key for s in statements}
    assert [r["target_slug"] for r in stored[attribution.key]["references"]] == [
        "connection"
    ]
    assert state_store.verify_state(api, tmp_path, only=("links",))["ok"] is True


def test_the_api_is_the_default_store(tmp_path: Path) -> None:
    sentinel = object()
    with (
        patch.object(main, "api_state", return_value=sentinel),
        patch.object(main, "link_local", return_value={}) as run,
    ):
        main.main(["link", "--links-file", str(tmp_path / "l.json")])
    assert run.call_args.kwargs["state"] is sentinel

    with patch.object(main, "link_local", return_value={}) as run:
        main.main(
            ["link", "--links-file", str(tmp_path / "l.json"), "--state", "files"]
        )
    assert "state" not in run.call_args.kwargs


# ── One state folder for every command ──


def test_every_command_takes_its_files_from_the_state_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    curator = (tmp_path / ".curator").resolve()
    with (
        patch.object(main, "api_state", return_value=object()),
        patch.object(main, "generate_local", return_value={}) as gen,
        patch.object(main, "consolidate_local", return_value={}) as con,
        patch.object(main, "link_local", return_value={}) as lnk,
        patch.object(
            main, "render_local", return_value={"broken_links": {"count": 0}}
        ) as ren,
    ):
        assert main.main(["generate", "--dry-run"]) == 0
        assert main.main(["consolidate"]) == 0
        assert main.main(["link"]) == 0
        assert main.main(["render"]) == 0

    assert gen.call_args.args[0] == curator / "prose.json"
    assert gen.call_args.kwargs["links_path"] == curator / "links.json"
    assert gen.call_args.kwargs["facets_path"] == curator / "facets.json"
    assert con.call_args.args[0] == curator / "facets.json"
    assert con.call_args.kwargs["prose_path"] == curator / "prose.json"
    assert lnk.call_args.args[0] == curator / "links.json"
    assert ren.call_args.args[1:4] == (
        curator / "links.json",
        curator / "prose.json",
        curator / "facets.json",
    )


def test_a_command_that_writes_state_will_not_guess_where(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WIKI_REPO_PATH", raising=False)
    with (
        patch.object(main, "api_state", return_value=object()),
        patch.object(main, "generate_local", side_effect=AssertionError("ran")),
        patch.object(main, "link_local", side_effect=AssertionError("ran")),
        patch.object(main, "consolidate_local", side_effect=AssertionError("ran")),
    ):
        assert main.main(["generate"]) == 2
        assert main.main(["link"]) == 2
        assert main.main(["consolidate"]) == 2


def test_a_file_flag_still_overrides_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    with (
        patch.object(main, "api_state", return_value=object()),
        patch.object(main, "generate_local", return_value={}) as gen,
    ):
        main.main(["generate", "--prose-file", str(tmp_path / "other.json")])
    assert gen.call_args.args[0] == (tmp_path / "other.json").resolve()
    assert (
        gen.call_args.kwargs["links_path"]
        == (tmp_path / ".curator" / "links.json").resolve()
    )


# ── The thin list ──


def _thin(slug: str, **over: Any) -> WcsWikiThinEntity:
    return WcsWikiThinEntity(
        slug=slug, name=slug, kind="concept", sources=1, references=0, page=slug, **over
    )


def test_the_thin_list_is_made_the_api_s_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(state_backend, "THIN_PER_POST", 2)
    monkeypatch.setattr(state_backend, "THIN_REMOVES_PER_POST", 1)
    api = FakeApi()
    store = state_backend.ApiState(api)
    first = store.save_thin([_thin("a"), _thin("b"), _thin("c"), _thin("d")])
    assert first == {"total": 4, "written": 4, "removed": 0}
    assert [len(p.entities) for p in api.thin_posts] == [2, 2]

    api.thin_posts.clear()
    # c changed; a and d have stopped being thin; b is as it was
    second = store.save_thin([_thin("b"), _thin("c", definition="Now defined.")])
    assert second == {"total": 2, "written": 1, "removed": 2}
    assert [(len(p.entities), p.remove) for p in api.thin_posts] == [
        (1, []),
        (0, ["a"]),
        (0, ["d"]),
    ]
    assert set(api.thin) == {"b", "c"}
    assert api.thin["c"]["definition"] == "Now defined."


def test_without_the_api_there_is_no_thin_list_to_store() -> None:
    assert state_backend.FileState().save_thin([_thin("a")]) is None


def test_a_local_consolidate_stores_the_thin_list_and_still_writes_the_file(
    tmp_path: Path,
) -> None:
    from .test_units import RouterLLM
    from .test_units import _export as _units_export

    api = FakeApi()
    path = tmp_path / "facets.json"
    with (
        patch.object(flow, "_fetch_export", return_value=_units_export()),
        patch.object(link, "llm_from_env", return_value=(RouterLLM(), "fake/m")),
    ):
        summary = flow.consolidate_local(
            path,
            prose_path=tmp_path / "prose.json",
            links_path=tmp_path / "links.json",
            state=state_backend.ApiState(api),
        )
    assert (tmp_path / "thin-review.md").exists()
    assert api.thin and summary["thin_written"]["total"] == len(api.thin)
