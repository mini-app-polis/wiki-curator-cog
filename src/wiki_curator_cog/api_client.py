"""Internal API client for wiki-curator-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the read-only WCS notes endpoints and the pipeline_evaluations write
endpoint on api-kaianolevine-com.

Auth: Clerk M2M JWT via KaianoApiClient (Project Keystone). Machine secret
is read from KAIANO_API_CLERK_MACHINE_SECRET at client construction time;
the shared client handles token acquisition, caching, and Authorization:
Bearer header injection.

Scope required: wcs_admin (the curator reads all notes regardless of
visibility).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterator

from mini_app_polis.api import KaianoApiClient

from .models import WcsNote


class WikiCuratorApiClient:
    """Typed client for api-kaianolevine-com endpoints consumed by the curator."""

    def __init__(self) -> None:
        # KaianoApiClient.from_env() reads KAIANO_API_BASE_URL and
        # KAIANO_API_CLERK_MACHINE_SECRET. It handles Clerk M2M JWT
        # acquisition, caching, and refresh.
        self._client = KaianoApiClient.from_env()

    # ── Read endpoints ────────────────────────────────────────────────────

    def list_notes_all(
        self,
        *,
        session_type: str | None = None,
        visibility: str | None = None,
        since: dt.datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """GET /v1/wcs/notes/all — admin list, all visibility levels.

        Returns the raw envelope dict; callers extract `data` and `meta`.
        Requires wcs_admin scope.

        Note: the `since` filter requires API support (Phase 1.5 work).
        Until it lands, callers paginate the full list and filter client-side.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if session_type is not None:
            params["session_type"] = session_type
        if visibility is not None:
            params["visibility"] = visibility
        if since is not None:
            params["since"] = since.isoformat()
        return self._client.get("/v1/wcs/notes/all", params=params)

    def iter_all_notes(
        self,
        *,
        page_size: int = 100,
        since: dt.datetime | None = None,
    ) -> Iterator[WcsNote]:
        """Iterate every note from /v1/wcs/notes/all, transparently paginating.

        Yields WcsNote instances. Useful for backfill mode.
        """
        offset = 0
        while True:
            envelope = self.list_notes_all(
                limit=page_size, offset=offset, since=since
            )
            data = envelope.get("data") or []
            if not data:
                return
            for item in data:
                yield WcsNote.model_validate(item)
            if len(data) < page_size:
                return
            offset += page_size

    def get_note(self, note_id: str) -> WcsNote:
        """GET /v1/wcs/notes/{note_id}. Requires owner auth or wcs_admin."""
        envelope = self._client.get(f"/v1/wcs/notes/{note_id}")
        return WcsNote.model_validate(envelope["data"])

    # ── Write endpoints ───────────────────────────────────────────────────

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
        """POST /v1/evaluations — emit one pipeline-evaluation finding.

        Used by the curator in automated mode to record judgment calls
        (ambiguous merges deferred to terminology pages, stub creations,
        upstream quality issues, etc). Standard dimension slugs are
        documented in wcs-wiki/CLAUDE.md under "Automated mode".

        Matches the PipelineEvaluationCreate schema (extra=forbid).
        """
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
