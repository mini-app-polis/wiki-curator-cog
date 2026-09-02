"""Internal API client for wiki-curator-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the bulk wiki export read endpoint and the pipeline_evaluations write
endpoint on api-kaianolevine-com.

Auth: this cog's own named API key, read from WIKI_CURATOR_COG_API_KEY by the
shared client, which derives that variable from MACHINE_NAME below. The key
identifies the cog, so the API's audit trail records which cog read the corpus
rather than merely that a cog did.

Falls back to the shared Clerk machine secret when the key is unset, which is
the previous behaviour and the rollback path.

Scopes required: ``wcs.notes.read`` for the export — the renderer reads the
full corpus regardless of per-source visibility — and
``pipeline.evaluations.write`` for reporting its own run. Note that reading
everything is a read role, not an admin one: this cog has no business holding
``wcs.grants.write``, which decides who may see what.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api import KaianoApiClient

from .models import WcsWikiExport

#: This cog's name in api-kaianolevine-com's identity_registry.MACHINES. The
#: shared client derives WIKI_CURATOR_COG_API_KEY from it, and the API derives
#: the same variable from the same name.
MACHINE_NAME = "wiki-curator-cog"


class WikiCuratorApiClient:
    """Typed client for api-kaianolevine-com endpoints consumed by the renderer."""

    def __init__(self) -> None:
        self._client = KaianoApiClient.from_env(MACHINE_NAME)

    def fetch_export(self) -> WcsWikiExport:
        """GET /v1/wcs/wiki/export — full canonical corpus in one call."""
        envelope = self._client.get("/v1/wcs/wiki/export")
        return WcsWikiExport.model_validate(envelope["data"])

    def post_run_evaluation(
        self,
        *,
        dimension: str,
        severity: str,
        finding: str,
        source: str = "conformance_llm",
        flow_name: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """POST /v1/evaluations — emit one pipeline-evaluation finding."""
        payload: dict[str, Any] = {
            "repo": "wiki-curator-cog",
            "source": source,
            "dimension": dimension,
            "severity": severity,
            "finding": finding,
        }
        if flow_name is not None:
            payload["flow_name"] = flow_name
        if run_id is not None:
            payload["run_id"] = run_id
        self._client.post("/v1/evaluations", payload)
