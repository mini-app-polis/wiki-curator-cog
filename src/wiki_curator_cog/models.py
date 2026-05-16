"""Pydantic models for the wiki-curator-cog.

The curator's primary input is a WCS note from api-kaianolevine-com. The
shape mirrors `WcsNoteItem` in the API schema; the curator does not need
write models (it never POSTs notes).

Findings emitted to /v1/evaluations follow the existing
PipelineEvaluationCreate shape and are constructed inline in api_client.py;
no model is needed for them here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WcsSessionType = Literal["private_lesson", "group_class", "other"]
WcsVisibility = Literal["private", "public"]


class WcsNote(BaseModel):
    """One WCS note as returned by GET /v1/wcs/notes/{id} (the API's WcsNoteItem).

    Field shapes exactly match the upstream schema. The curator reads notes_json
    as an unstructured dict — its inner shape is documented in
    wcs-wiki/CLAUDE.md ("The notes_json schema" section) but the curator
    treats missing or unknown sub-fields gracefully rather than failing on them.
    """

    id: uuid.UUID
    transcript_id: uuid.UUID
    title: str | None = None
    session_date: dt.date | None = None
    session_type: str
    instructors: list[str] = Field(default_factory=list)
    students: list[str] = Field(default_factory=list)
    organization: str = ""
    is_default_visible: bool
    visibility: str
    model: str
    provider: str
    notes_json: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime

    model_config = ConfigDict(extra="ignore")


class CuratorState(BaseModel):
    """Curator-internal state persisted between runs.

    Stored as JSON on local disk at the path given by WIKI_STATE_PATH.
    Used by incremental mode to fetch only notes newer than the last run.
    """

    last_run_at: dt.datetime | None = None
    last_successful_note_id: uuid.UUID | None = None
    curator_version_at_last_run: int = 0

    model_config = ConfigDict(extra="ignore")
