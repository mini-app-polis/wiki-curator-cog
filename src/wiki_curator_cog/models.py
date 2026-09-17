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
    """One wiki entity — a concept, technique, pattern or drill."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    slug: str = Field(
        ..., description="Lowercase, hyphen-separated canonical identifier."
    )
    canonical_name: str = Field(
        ..., description="Canonical, post-collapse display name."
    )
    kind: WcsEntityKind = Field(
        ...,
        description="Discriminator for the entity kind (concept, technique, pattern, drill).",
    )
    overview_md: str = Field(
        "", description="Markdown overview rendered at the top of the entity's page."
    )
    status: str = Field(
        "", description="Lifecycle status flag (e.g., stub, draft, mature)."
    )
    external_origin: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional external attribution (book, video, etc.).",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Variant names that resolve to this canonical row.",
    )

    model_config = ConfigDict(extra="ignore")


class WcsInstructor(BaseModel):
    """One instructor, with the prose sections their page is built from."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    slug: str = Field(
        ..., description="Lowercase, hyphen-separated canonical identifier."
    )
    canonical_name: str = Field(
        ..., description="Canonical, post-collapse display name."
    )
    background_md: str = Field(
        "", description="Markdown background section of the instructor's page."
    )
    teaching_themes_md: str = Field(
        "",
        description="Markdown summary of the instructor's recurring teaching themes.",
    )
    notable_framings_md: str = Field(
        "", description="Markdown notes on framings this instructor is known for."
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Variant names that resolve to this canonical row.",
    )

    model_config = ConfigDict(extra="ignore")


class WcsSource(BaseModel):
    """One transcribed session the wiki draws from."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    transcript_id: uuid.UUID = Field(
        ..., description="Identifier of the upstream transcript."
    )
    title: str | None = Field(None, description="Topic or display title.")
    session_date: dt.date | None = Field(
        None, description="Calendar date of the lesson session."
    )
    session_type: str = Field(
        ..., description="Session type — e.g. private_lesson, group_class, other."
    )
    instructors_raw: list[str] = Field(
        default_factory=list,
        description="Verbatim upstream instructor names before alias resolution.",
    )
    students_raw: list[str] = Field(
        default_factory=list,
        description="Verbatim upstream student names before alias resolution.",
    )
    organization: str = Field(
        "", description="Organization, studio, or event context for the session."
    )
    visibility: str = Field(
        ..., description="Coarse access-control flag (private vs. public)."
    )
    is_default_visible: bool = Field(
        ..., description="Whether the source is shown in the default catalog."
    )
    created_at: dt.datetime = Field(..., description="Timestamp this row was created.")

    model_config = ConfigDict(extra="ignore")


class WcsAttribution(BaseModel):
    """Something an instructor did with an entity in one source.

    Covers the taught, demonstrated and drilled kinds as well as mistakes
    and corrections; which fields are populated depends on
    ``attribution_kind``.
    """

    id: uuid.UUID = Field(..., description="Unique identifier.")
    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    entity_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS entity this attribution is about."
    )
    instructor_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS instructor."
    )
    attribution_kind: str = Field(
        ..., description="Discriminator for the attribution row type."
    )
    prose: str = Field("", description="Free-text content for this row.")
    raw_term: str = Field(
        "", description="Raw term string as it appeared in the upstream extraction."
    )
    position: int = Field(
        0, description="Ordinal position of the row within its source."
    )
    drill_goal: str | None = Field(
        None,
        description="What the drill is meant to achieve, when this row is a drill.",
    )
    drill_steps: list[str] | None = Field(
        None, description="Ordered steps of the drill, when this row is a drill."
    )
    mistake_text: str | None = Field(
        None,
        description="The mistake described, when this row is a mistake or correction.",
    )
    correction_text: str | None = Field(
        None,
        description="The correction given, when this row is a mistake or correction.",
    )
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )

    model_config = ConfigDict(extra="ignore")


class WcsDefinition(BaseModel):
    """A term as it was defined in one source."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    entity_id: uuid.UUID = Field(..., description="Identifier of the WCS entity.")
    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    instructor_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS instructor."
    )
    term: str = Field(
        "", description="The term being defined, as stated in the source."
    )
    definition: str = Field(
        "", description="Definition prose attached to an entity for one source."
    )
    position: int = Field(
        0, description="Ordinal position of the row within its source."
    )
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )

    model_config = ConfigDict(extra="ignore")


class WcsRelation(BaseModel):
    """A directed link from one entity to another."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    from_entity_id: uuid.UUID = Field(
        ..., description="Identifier of the entity the relation points from."
    )
    to_entity_id: uuid.UUID = Field(
        ..., description="Identifier of the entity the relation points to."
    )
    relation_kind: str = Field(
        ..., description="Discriminator for the entity-to-entity relation type."
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    prose: str = Field("", description="Free-text content for this row.")
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )

    model_config = ConfigDict(extra="ignore")


class WcsDrillPurpose(BaseModel):
    """The skill a drill is meant to build."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    drill_entity_id: uuid.UUID = Field(
        ..., description="Identifier of the drill entity this purpose belongs to."
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    skill_name: str = Field(
        "", description="Human-readable skill name this row references."
    )
    skill_slug: str = Field(
        "", description="Lowercase, hyphen-separated slug of the referenced skill."
    )
    prose: str = Field("", description="Free-text content for this row.")
    focus_context: str = Field(
        "", description="Focus or context hint that scopes how this row applies."
    )
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )

    model_config = ConfigDict(extra="ignore")


class WcsTechniqueRequirement(BaseModel):
    """A skill a technique depends on."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    technique_entity_id: uuid.UUID = Field(
        ...,
        description="Identifier of the technique entity this requirement belongs to.",
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    skill_name: str = Field(
        "", description="Human-readable skill name this row references."
    )
    skill_slug: str = Field(
        "", description="Lowercase, hyphen-separated slug of the referenced skill."
    )
    prose: str = Field("", description="Free-text content for this row.")
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )

    model_config = ConfigDict(extra="ignore")


class WcsReference(BaseModel):
    """A name referenced in a source, with the context it appeared in."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    referenced_name: str = Field(
        "", description="Raw mention name from the extraction."
    )
    context: str = Field("", description="Free-text context for the reference.")
    ref_type: str = Field(
        "", description="Kind of reference, as classified by the extraction."
    )
    origin: str = Field(
        "", description="Originating source or upstream attribution metadata."
    )
    created_at: dt.datetime | None = Field(
        None, description="Timestamp when this record was created."
    )

    model_config = ConfigDict(extra="ignore")


class WcsWikiExport(BaseModel):
    """Top-level export payload (envelope ``data`` field)."""

    entities: list[WcsEntity] = Field(
        default_factory=list, description="Every entity in the corpus."
    )
    instructors: list[WcsInstructor] = Field(
        default_factory=list, description="Every instructor in the corpus."
    )
    sources: list[WcsSource] = Field(
        default_factory=list, description="Every source session in the corpus."
    )
    attributions: list[WcsAttribution] = Field(
        default_factory=list, description="Every attribution row, across all sources."
    )
    definitions: list[WcsDefinition] = Field(
        default_factory=list, description="Every definition row, across all sources."
    )
    relations: list[WcsRelation] = Field(
        default_factory=list,
        description="Every entity-to-entity relation in the corpus.",
    )
    drill_purposes: list[WcsDrillPurpose] = Field(
        default_factory=list, description="Every drill-to-skill link in the corpus."
    )
    technique_requirements: list[WcsTechniqueRequirement] = Field(
        default_factory=list,
        description="Every technique-to-skill requirement in the corpus.",
    )
    references: list[WcsReference] = Field(
        default_factory=list,
        description="Every raw name mentioned in a source and not resolved to an entity.",
    )

    model_config = ConfigDict(extra="ignore")
