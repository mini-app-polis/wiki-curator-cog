"""Pydantic models for the wiki-curator-cog renderer.

The renderer's input is the bulk export from GET /v1/wcs/wiki/export.
Field shapes mirror the API's WcsWikiExportItem schema.

Findings emitted to /v1/evaluations follow PipelineEvaluationCreate and
are constructed inline in api_client.py.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WcsEntityKind = Literal["concept", "technique", "pattern", "drill"]


class WcsEntity(BaseModel):
    id: uuid.UUID
    slug: str
    canonical_name: str
    kind: WcsEntityKind
    overview_md: str = ""
    status: str = ""
    external_origin: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class WcsInstructor(BaseModel):
    id: uuid.UUID
    slug: str
    canonical_name: str
    background_md: str = ""
    teaching_themes_md: str = ""
    notable_framings_md: str = ""
    aliases: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class WcsSource(BaseModel):
    id: uuid.UUID
    transcript_id: uuid.UUID
    title: str | None = None
    session_date: dt.date | None = None
    session_type: str
    instructors_raw: list[str] = Field(default_factory=list)
    students_raw: list[str] = Field(default_factory=list)
    organization: str = ""
    visibility: str
    is_default_visible: bool
    created_at: dt.datetime

    model_config = ConfigDict(extra="ignore")


class WcsAttribution(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    entity_id: uuid.UUID
    instructor_id: uuid.UUID | None = None
    attribution_kind: str
    prose: str = ""
    raw_term: str = ""
    position: int = 0
    drill_goal: str | None = None
    drill_steps: list[str] | None = None
    mistake_text: str | None = None
    correction_text: str | None = None
    origin: str = ""

    model_config = ConfigDict(extra="ignore")


class WcsDefinition(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    source_id: uuid.UUID
    instructor_id: uuid.UUID | None = None
    term: str = ""
    definition: str = ""
    position: int = 0
    origin: str = ""

    model_config = ConfigDict(extra="ignore")


class WcsRelation(BaseModel):
    id: uuid.UUID
    from_entity_id: uuid.UUID
    to_entity_id: uuid.UUID
    relation_kind: str
    source_id: uuid.UUID | None = None
    prose: str = ""
    origin: str = ""

    model_config = ConfigDict(extra="ignore")


class WcsDrillPurpose(BaseModel):
    id: uuid.UUID
    drill_entity_id: uuid.UUID
    source_id: uuid.UUID | None = None
    skill_name: str = ""
    skill_slug: str = ""
    prose: str = ""
    focus_context: str = ""
    origin: str = ""

    model_config = ConfigDict(extra="ignore")


class WcsTechniqueRequirement(BaseModel):
    id: uuid.UUID
    technique_entity_id: uuid.UUID
    source_id: uuid.UUID | None = None
    skill_name: str = ""
    skill_slug: str = ""
    prose: str = ""
    origin: str = ""

    model_config = ConfigDict(extra="ignore")


class WcsReference(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    referenced_name: str = ""
    context: str = ""
    ref_type: str = ""
    origin: str = ""
    created_at: dt.datetime | None = None

    model_config = ConfigDict(extra="ignore")


class WcsWikiExport(BaseModel):
    """Top-level export payload (envelope ``data`` field)."""

    entities: list[WcsEntity] = Field(default_factory=list)
    instructors: list[WcsInstructor] = Field(default_factory=list)
    sources: list[WcsSource] = Field(default_factory=list)
    attributions: list[WcsAttribution] = Field(default_factory=list)
    definitions: list[WcsDefinition] = Field(default_factory=list)
    relations: list[WcsRelation] = Field(default_factory=list)
    drill_purposes: list[WcsDrillPurpose] = Field(default_factory=list)
    technique_requirements: list[WcsTechniqueRequirement] = Field(default_factory=list)
    references: list[WcsReference] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
