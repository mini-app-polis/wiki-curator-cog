"""The export is read through the shared client's typed method."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
import respx
from mini_app_polis.api import client as client_mod
from mini_app_polis.api.client import KaianoApiClient
from mini_app_polis.api.contract import WcsWikiExportItem

from wiki_curator_cog import flow

from .export_builders import WcsEntity, WcsWikiExport

URL = "https://example.test/v1/wcs/wiki/export"


def _answer(export: WcsWikiExportItem) -> dict:
    return {
        "data": export.model_dump(mode="json"),
        "meta": {"count": 1, "total": 1, "version": "test"},
    }


def _export() -> WcsWikiExportItem:
    return WcsWikiExport(
        entities=[
            WcsEntity(
                id=uuid.uuid4(),
                slug="anchor-step",
                canonical_name="Anchor Step",
                kind="concept",
            )
        ]
    )


@pytest.fixture(autouse=True)
def as_this_cog() -> Iterator[None]:
    """A client for a test host, with no copy of an earlier export kept."""
    client = KaianoApiClient(base_url="https://example.test", api_key="k")
    with (
        patch.dict(client_mod._ETAG_CACHE, clear=True),
        patch.object(KaianoApiClient, "from_env", return_value=client) as from_env,
    ):
        yield
    from_env.assert_called_with("wiki-curator-cog")


@respx.mock
def test_the_export_is_read_as_this_cog_and_validated() -> None:
    respx.get(URL).respond(200, json=_answer(_export()))

    fetched = flow._fetch_export()

    assert isinstance(fetched, WcsWikiExportItem)
    assert [e.slug for e in fetched.entities] == ["anchor-step"]


@respx.mock
def test_an_unchanged_export_is_not_downloaded_again() -> None:
    """A warm worker sends the ETag it holds; a 304 reuses the copy it kept."""
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(200, json=_answer(_export()), headers={"ETag": '"r1"'}),
        httpx.Response(304, headers={"ETag": '"r1"'}),
    ]

    first = flow._fetch_export()
    second = flow._fetch_export()

    assert route.calls[1].request.headers["If-None-Match"] == '"r1"'
    assert [e.slug for e in second.entities] == ["anchor-step"]
    # Each job gets a model of its own, so one job cannot alter the next's.
    assert first is not second
