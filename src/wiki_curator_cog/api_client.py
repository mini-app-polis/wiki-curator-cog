"""Internal API client for wiki-curator-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the bulk wiki export read endpoint and the pipeline_evaluations write
endpoint on api-kaianolevine-com.

Auth: Clerk M2M JWT via KaianoApiClient (Project Keystone). Machine secret
is read from KAIANO_API_CLERK_MACHINE_SECRET at client construction time.

Scope required: wcs_admin (the renderer reads the full export regardless
of per-source visibility).
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api import KaianoApiClient

from .models import WcsWikiExport


class WikiCuratorApiClient:
    """Typed client for api-kaianolevine-com endpoints consumed by the renderer."""

    def __init__(self) -> None:
        self._client = KaianoApiClient.from_env()

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
