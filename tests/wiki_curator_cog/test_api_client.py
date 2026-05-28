"""Tests for api_client.WikiCuratorApiClient."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

from wiki_curator_cog.api_client import WikiCuratorApiClient
from wiki_curator_cog.models import WcsWikiExport


def _export_payload() -> dict[str, Any]:
    entity_id = uuid.uuid4()
    return {
        "entities": [
            {
                "id": str(entity_id),
                "slug": "anchor-step",
                "canonical_name": "Anchor Step",
                "kind": "concept",
                "overview_md": "",
                "status": "draft",
                "external_origin": {},
                "aliases": [],
            }
        ],
        "instructors": [],
        "sources": [],
        "attributions": [],
        "definitions": [],
        "relations": [],
        "drill_purposes": [],
        "technique_requirements": [],
        "references": [],
    }


def test_fetch_export_validates_envelope() -> None:
    client = WikiCuratorApiClient()
    mock_http = MagicMock()
    mock_http.get.return_value = {
        "data": _export_payload(),
        "meta": {"count": 1},
    }
    client._client = mock_http

    export = client.fetch_export()

    assert isinstance(export, WcsWikiExport)
    assert len(export.entities) == 1
    assert export.entities[0].slug == "anchor-step"
    mock_http.get.assert_called_once_with("/v1/wcs/wiki/export")


def test_post_run_evaluation_posts_payload() -> None:
    client = WikiCuratorApiClient()
    mock_http = MagicMock()
    client._client = mock_http

    client.post_run_evaluation(
        dimension="quality",
        severity="info",
        finding="test finding",
        flow_name="wiki-curator-cog-export",
        run_id="run-123",
    )

    mock_http.post.assert_called_once()
    path, payload = mock_http.post.call_args[0]
    assert path == "/v1/evaluations"
    assert payload["repo"] == "wiki-curator-cog"
    assert payload["finding"] == "test finding"
    assert payload["flow_name"] == "wiki-curator-cog-export"
    assert payload["run_id"] == "run-123"
