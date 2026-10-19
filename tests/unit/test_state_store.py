"""Moving the curator's state to the API: nothing unknown sent, nothing lost.

``FakeApi`` behaves as the API's state endpoints do: a POST upserts the
entries it carries and replaces the header; a GET returns the whole
document through the contract model, as the client would.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mini_app_polis.api.contract import (
    WcsWikiFacetsState,
    WcsWikiLinksState,
    WcsWikiPagesWriteResult,
    WcsWikiProseState,
    WcsWikiStateWriteResult,
    WcsWikiThinState,
    WcsWikiThinWriteResult,
)

from wiki_curator_cog import main, state_store

KEY = "attribution:0012e47a-7fce-4c01-9322-0a446fb8e05f"
ENTITY = "30f62150-c875-445e-a0c7-33c3c924548b"

MODELS = {
    "links": (WcsWikiLinksState, "statements"),
    "facets": (WcsWikiFacetsState, "entities"),
    "prose": (WcsWikiProseState, "entities"),
}


class FakeApi:
    def __init__(self) -> None:
        # As the API answers before anything is written: empty headers.
        self.docs: dict[str, dict[str, Any]] = {
            "links": {"linker_version": "", "model": "", "statements": {}},
            "facets": {"consolidator_versions": {}, "model": "", "entities": {}},
            "prose": {"generator_version": "", "model": "", "entities": {}},
        }
        self.posts: dict[str, int] = {name: 0 for name in MODELS}
        self.gets: dict[str, int] = {name: 0 for name in MODELS}
        #: Page documents as the API stores them, by slug (MINI-96).
        self.pages: dict[str, dict[str, Any]] = {}
        self.page_posts: list[Any] = []
        #: Thin entities as the API stores them, by slug (MINI-101).
        self.thin: dict[str, dict[str, Any]] = {}
        self.thin_posts: list[Any] = []

    def get_wcs_wiki_thin(self) -> WcsWikiThinState:
        return WcsWikiThinState.model_validate(
            {"entities": [self.thin[s] for s in sorted(self.thin)]}
        )

    def put_wcs_wiki_thin(self, payload: Any) -> WcsWikiThinWriteResult:
        self.thin_posts.append(payload)
        for e in payload.entities:
            self.thin[e.slug] = e.model_dump(mode="json")
        gone = [s for s in payload.remove if self.thin.pop(s, None) is not None]
        return WcsWikiThinWriteResult(
            written=len(payload.entities), removed=len(gone), total=len(self.thin)
        )

    def put_wcs_wiki_pages(self, payload: Any) -> WcsWikiPagesWriteResult:
        self.page_posts.append(payload)
        created, changed = [], []
        for page in payload.pages:
            document = page.model_dump(mode="json")
            if page.slug not in self.pages:
                created.append(page.slug)
            elif self.pages[page.slug] != document:
                changed.append(page.slug)
            self.pages[page.slug] = document
        gone = [s for s in payload.remove if self.pages.pop(s, None) is not None]
        return WcsWikiPagesWriteResult(
            written=len(payload.pages),
            removed=len(gone),
            total=len(self.pages),
            created=sorted(created),
            changed=sorted(changed),
            removed_slugs=sorted(gone),
        )

    def __getattr__(self, attr: str) -> Any:
        verb, name = attr.removeprefix("get_wcs_wiki_"), None
        for n in MODELS:
            if attr in (f"get_wcs_wiki_{n}", f"put_wcs_wiki_{n}"):
                name = n
                verb = attr.split("_", 1)[0]
        if name is None:
            raise AttributeError(attr)
        model, key = MODELS[name]
        if verb == "get":

            def get() -> Any:
                self.gets[name] += 1
                return model.model_validate(self.docs[name])

            return get

        def put(doc: Any) -> WcsWikiStateWriteResult:
            raw = doc.model_dump(mode="json")
            self.posts[name] += 1
            self.docs[name].update({k: v for k, v in raw.items() if k != key})
            self.docs[name][key].update(raw[key])
            return WcsWikiStateWriteResult(
                written=len(raw[key]), total=len(self.docs[name][key])
            )

        return put


def _write_files(d: Path, *, facets_legacy: bool = False) -> None:
    (d / "links.json").write_text(
        json.dumps(
            {
                "linker_version": "3",
                "model": "m",
                "statements": {
                    KEY: {
                        "linker_version": "3",
                        "model": "m",
                        "subject_slug": "frame",
                        "source_id": ENTITY,
                        "session_date": "2026-09-16",
                        "text": "Connect at the shoulder blade.",
                        "references": [
                            {
                                "name": "connection",
                                "target_slug": "connection",
                                "target_entity_id": None,
                                "polarity": "assert",
                            }
                        ],
                    },
                    "definition:x": {
                        "linker_version": "3",
                        "model": "m",
                        "subject_slug": "anchor",
                        "source_id": ENTITY,
                        "session_date": "2026-09-16",
                        "text": "Nothing referred to.",
                        "references": [],
                    },
                },
            }
        )
    )
    header = (
        {"consolidator_version": "5"}
        if facets_legacy
        else {"consolidator_versions": {"concept": "5", "drill": "5", "pattern": "5"}}
    )
    (d / "facets.json").write_text(
        json.dumps(
            {
                **header,
                "model": "m",
                "entities": {
                    "stationary-anchor": {
                        "parent": "anchor",
                        "same": False,
                        "reason": "a kind of anchor",
                        "version": "5",
                        "model": "m",
                        "decided_at": "2026-10-16",
                    },
                    "anchor": {
                        "parent": None,
                        "same": False,
                        "reason": "",
                        "version": "5",
                        "model": "m",
                        "decided_at": "2026-10-16",
                    },
                },
            }
        )
    )
    (d / "prose.json").write_text(
        json.dumps(
            {
                "generator_version": "4",
                "model": "m",
                "entities": {
                    # laid out as the generator writes an entry
                    "anchor": {
                        "revisions": [
                            {
                                "by": "llm",
                                "at": "2026-10-15",
                                "model": "m",
                                "statements": 2,
                                "prose": "Old.",
                            },
                            {
                                "by": "page",
                                "at": "2026-10-16",
                                "definition": "Edited.",
                                "context": "",
                            },
                        ],
                        "integrated": [KEY],
                        "entity_id": ENTITY,
                        "generator_version": "4",
                        "model": "m",
                        "generated_at": "2026-10-16",
                        "definition": "The settle.",
                        "context": "",
                    }
                },
            }
        )
    )


def test_an_import_verifies_exactly(tmp_path: Path) -> None:
    _write_files(tmp_path)
    api = FakeApi()
    summary = state_store.import_state(api, tmp_path)
    assert summary["links"] == {"entries": 2, "chunks": 1, "total_in_api": 2}
    assert summary["prose"]["entries"] == 1
    result = state_store.verify_state(api, tmp_path)
    assert result["ok"] is True
    assert result["links"]["changed"] == 0


def test_chunks_add_up_and_a_rerun_changes_nothing(tmp_path: Path) -> None:
    _write_files(tmp_path)
    api = FakeApi()
    state_store.import_state(api, tmp_path, chunk=1)
    assert api.posts == {"links": 2, "facets": 2, "prose": 1}
    before = json.dumps(api.docs, sort_keys=True)
    state_store.import_state(api, tmp_path, chunk=1)
    assert json.dumps(api.docs, sort_keys=True) == before
    assert state_store.verify_state(api, tmp_path)["ok"] is True


def test_an_unknown_field_stops_the_import_before_any_write(tmp_path: Path) -> None:
    _write_files(tmp_path)
    raw = json.loads((tmp_path / "prose.json").read_text())
    raw["entities"]["anchor"]["revisions"][0]["tokens"] = 123
    (tmp_path / "prose.json").write_text(json.dumps(raw))
    api = FakeApi()
    with pytest.raises(state_store.UnknownFieldsError, match="revisions\\[0\\].tokens"):
        state_store.import_state(api, tmp_path)
    assert api.posts == {"links": 0, "facets": 0, "prose": 0}


def test_verify_names_what_is_missing_changed_or_different(tmp_path: Path) -> None:
    _write_files(tmp_path)
    api = FakeApi()
    state_store.import_state(api, tmp_path)
    del api.docs["links"]["statements"]["definition:x"]
    api.docs["facets"]["entities"]["anchor"]["reason"] = "edited"
    api.docs["prose"]["generator_version"] = "5"

    result = state_store.verify_state(api, tmp_path)
    assert result["ok"] is False
    assert result["links"]["missing"] == 1
    assert result["facets"]["examples"] == ["anchor"]
    assert result["prose"]["header"] == {"generator_version": {"file": "4", "api": "5"}}


def test_the_older_facets_layout_imports_with_per_group_versions(
    tmp_path: Path,
) -> None:
    _write_files(tmp_path, facets_legacy=True)
    api = FakeApi()
    state_store.import_state(api, tmp_path)
    assert api.docs["facets"]["consolidator_versions"] == {
        "concept": "5",
        "drill": "5",
        "pattern": "5",
    }
    assert state_store.verify_state(api, tmp_path)["ok"] is True


def test_the_commands_need_a_state_dir_and_verify_sets_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main.main(["state-verify"]) == 2
    monkeypatch.setattr(
        main, "state_verify_local", lambda d: {"state_dir": str(d), "ok": False}
    )
    assert main.main(["state-verify", "--state-dir", str(tmp_path)]) == 1
    monkeypatch.setattr(
        main, "state_verify_local", lambda d: {"state_dir": str(d), "ok": True}
    )
    assert main.main(["state-verify", "--state-dir", str(tmp_path)]) == 0
