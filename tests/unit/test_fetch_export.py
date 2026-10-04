"""The export is read through the shared client's typed method."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from mini_app_polis.api.client import KaianoApiClient
from mini_app_polis.api.contract import WcsWikiExportItem

from wiki_curator_cog import flow

from .export_builders import WcsEntity, WcsWikiExport


def _answer(export: WcsWikiExportItem) -> dict:
    return {
        "data": export.model_dump(mode="json"),
        "meta": {"count": 1, "total": 1, "version": "test"},
    }


def test_the_export_is_read_as_this_cog_and_validated() -> None:
    export = WcsWikiExport(
        entities=[
            WcsEntity(
                id=uuid.uuid4(),
                slug="anchor-step",
                canonical_name="Anchor Step",
                kind="concept",
            )
        ]
    )
    client = KaianoApiClient(base_url="https://example.test", api_key="k")
    client.get = MagicMock(return_value=_answer(export))  # type: ignore[method-assign]

    with patch.object(KaianoApiClient, "from_env", return_value=client) as from_env:
        fetched = flow._fetch_export()

    from_env.assert_called_once_with("wiki-curator-cog")
    client.get.assert_called_once_with("/v1/wcs/wiki/export", None)
    assert isinstance(fetched, WcsWikiExportItem)
    assert [e.slug for e in fetched.entities] == ["anchor-step"]
