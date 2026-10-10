"""Fixtures for running an export end to end.

What is real: the entry point (``flow.export_run``), config loading, the
clone, the render, staging, the commit and the push. The wiki remote is a
bare repository on disk, so git does everything it does in production except
cross a network.

What is stubbed: the Kaiano API, at the HTTP boundary (TEST-007). The export
GET and the run report's ``POST /v1/notify`` both go through the real
``KaianoApiClient``; respx answers them, and any other request fails the
test rather than leaving the machine.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from git import Repo
from mini_app_polis.api.contract import WcsWikiExportItem

API = "https://api.example"
API_KEY = "wiki-curator-test-key"

#: A derived page the remote holds that no export renders, so a run has to
#: remove it.
STALE_PAGE = "concepts/retired-concept.md"


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare wcs-wiki remote on ``main``: the skeleton plus one stale page."""
    bare = tmp_path / "wcs-wiki.git"
    Repo.init(bare, bare=True, initial_branch="main")

    seed = Repo.init(tmp_path / "seed", initial_branch="main")
    root = Path(seed.working_tree_dir or "")
    (root / "CLAUDE.md").write_text("# CLAUDE.md\n")
    (root / STALE_PAGE).parent.mkdir(parents=True)
    (root / STALE_PAGE).write_text("# Retired\n")
    seed.index.add(["CLAUDE.md", STALE_PAGE])
    with seed.config_writer() as cw:
        cw.set_value("user", "name", "seed")
        cw.set_value("user", "email", "seed@example.com")
    seed.index.commit("seed")
    seed.create_remote("origin", str(bare)).push("main:main")
    return bare


@pytest.fixture
def run_env(origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point a run at ``origin`` and the stub API. Returns the clone path.

    A filesystem remote is not HTTPS, so no GH_TOKEN is needed and the push
    preflight is skipped, as it is for an SSH deploy key.
    """
    clone = tmp_path / "clone"
    monkeypatch.setenv("KAIANO_API_BASE_URL", API)
    monkeypatch.setenv("WIKI_CURATOR_COG_API_KEY", API_KEY)
    monkeypatch.setenv("WIKI_REPO_URL", str(origin))
    monkeypatch.setenv("WIKI_REPO_PATH", str(clone))
    monkeypatch.setenv("WIKI_BRANCH", "main")
    monkeypatch.setenv("RUN_TIMEOUT_SECONDS", "0")
    return clone


def envelope(data: object) -> dict:
    """The API's success envelope around ``data``."""
    return {"data": data, "meta": {"count": 1, "total": 1, "version": "v1"}}


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """The Kaiano API. Routes are named ``export`` and ``notify``.

    ``export`` answers 503 until a test sets what it returns, so a test that
    forgets fails loudly instead of rendering nothing.
    """
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.get("/v1/wcs/wiki/export", name="export").mock(
            return_value=httpx.Response(503)
        )
        router.post("/v1/notify", name="notify").mock(
            return_value=httpx.Response(
                200,
                json=envelope({"forwarded": True, "event": "notify", "reason": "sent"}),
            )
        )
        yield router


def serve_export(api: respx.MockRouter, export: WcsWikiExportItem) -> None:
    api.routes["export"].mock(
        return_value=httpx.Response(200, json=envelope(export.model_dump(mode="json")))
    )


def remote_files(origin: Path) -> set[str]:
    """Every path on the remote's ``main``."""
    tree = Repo(origin).commit("main").tree
    return {str(blob.path) for blob in tree.traverse() if blob.type == "blob"}


def remote_head(origin: Path) -> str:
    return Repo(origin).commit("main").hexsha


def today() -> str:
    return dt.date.today().isoformat()
