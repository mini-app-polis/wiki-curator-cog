"""Stateless renderer: WcsWikiExport → markdown bundle.

Pure-ish module. Takes a canonical export and produces an in-memory map
of {relative_path: file_text} for the entire wiki bundle per
wcs-wiki/CLAUDE.md v1.1.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import markdown_utils as md
from .models import (
    WcsAttribution,
    WcsDefinition,
    WcsDrillPurpose,
    WcsEntity,
    WcsInstructor,
    WcsReference,
    WcsRelation,
    WcsSource,
    WcsTechniqueRequirement,
    WcsWikiExport,
)

_SLUG_MAX_LEN: int = 80

_TAUGHT_KINDS: frozenset[str] = frozenset({"taught", "demonstrated", "drilled"})
_MISTAKE_KINDS: frozenset[str] = frozenset({"mistake", "correction"})


@dataclass
class ExportIndexes:
    entities_by_id: dict[uuid.UUID, WcsEntity]
    entities_by_slug: dict[str, WcsEntity]
    instructors_by_id: dict[uuid.UUID, WcsInstructor]
    instructors_by_slug: dict[str, WcsInstructor]
    sources_by_id: dict[uuid.UUID, WcsSource]
    source_slug_by_id: dict[uuid.UUID, str]
    attributions_by_entity: dict[uuid.UUID, list[WcsAttribution]]
    attributions_by_source: dict[uuid.UUID, list[WcsAttribution]]
    definitions_by_entity: dict[uuid.UUID, list[WcsDefinition]]
    definitions_by_source: dict[uuid.UUID, list[WcsDefinition]]
    relations_by_entity: dict[uuid.UUID, list[WcsRelation]]
    drill_purposes_by_entity: dict[uuid.UUID, list[WcsDrillPurpose]]
    technique_requirements_by_entity: dict[uuid.UUID, list[WcsTechniqueRequirement]]
    references_by_source: dict[uuid.UUID, list[WcsReference]]
    relations_by_source: dict[uuid.UUID, list[WcsRelation]]
    canonical_instructors_by_source: dict[uuid.UUID, list[str]]
    #: Raw instructor names no canonical instructor matched. These are
    #: slugified and used anyway, which means the attributions credited
    #: to them land on no instructor page at all.
    unresolved_instructors: list[str] = field(default_factory=list)
    #: (slug, description) for source slugs claimed by more than one
    #: source. The later source wins the bundle key and the earlier one
    #: is never written.
    slug_collisions: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RenderStats:
    entity_count: int = 0
    concept_count: int = 0
    technique_count: int = 0
    pattern_count: int = 0
    drill_count: int = 0
    instructor_count: int = 0
    source_count: int = 0
    observations: list[str] = field(default_factory=list)
    #: (reason, reference) for everything this render could not resolve.
    #: observations go into log.md and are read by a person browsing the
    #: wiki; these are for the run report and are read by nobody unless
    #: something is wrong.
    dropped: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _ViewSpec:
    slug: str
    filter_description: str
    instructors_includes: tuple[str, ...] = ()
    students_includes: tuple[str, ...] = ()


REQUIRED_VIEWS: tuple[_ViewSpec, ...] = (
    _ViewSpec(
        slug="kaianos-canon",
        filter_description="sources where instructors contains kaiano",
        instructors_includes=("kaiano",),
    ),
    _ViewSpec(
        slug="kate-as-student",
        filter_description="sources where students contains kate",
        students_includes=("kate",),
    ),
    _ViewSpec(
        slug="full-model",
        filter_description="all sources, chronologically",
    ),
)


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if len(s) > _SLUG_MAX_LEN:
        truncated = s[:_SLUG_MAX_LEN]
        last_hyphen = truncated.rfind("-")
        s = truncated[:last_hyphen] if last_hyphen > 0 else truncated
    return s


def _session_type_slug(session_type: str) -> str:
    return slugify(session_type.replace("_", "-"))


def _entity_directory(kind: str) -> str:
    if kind == "concept":
        return "concepts"
    if kind == "drill":
        return "drills"
    if kind == "pattern":
        return "patterns"
    return "techniques"


def _entity_page_type(kind: str) -> str:
    if kind == "concept":
        return "concept"
    if kind == "drill":
        return "drill"
    if kind == "pattern":
        return "pattern"
    return "technique"


def _entity_path(entity: WcsEntity) -> str:
    return f"{_entity_directory(entity.kind)}/{entity.slug}.md"


def _resolve_raw_instructor(
    raw: str,
    instructors_by_slug: dict[str, WcsInstructor],
) -> str | None:
    key = raw.strip().lower()
    if not key:
        return None
    for instructor in instructors_by_slug.values():
        if instructor.slug.lower() == key:
            return instructor.slug
        if instructor.canonical_name.strip().lower() == key:
            return instructor.slug
        for alias in instructor.aliases:
            if alias.strip().lower() == key:
                return instructor.slug
    for slug, instructor in instructors_by_slug.items():
        if key in slug or key in instructor.canonical_name.lower():
            return instructor.slug
    return None


def _resolve_instructors_raw(
    raw_names: Iterable[str],
    instructors_by_slug: dict[str, WcsInstructor],
) -> tuple[list[str], list[str]]:
    """Canonical slugs for these raw names, and the ones that did not match.

    An unmatched name is still slugified and used, because dropping it
    would lose the attribution entirely. But the slug it produces matches
    no instructor in the index, so every extraction-origin attribution
    and definition credited to it appears on no instructor page — and the
    entity and source pages render normally, which is exactly why nobody
    notices. The second return value is what makes that sayable.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for raw in raw_names:
        if not raw or not str(raw).strip():
            continue
        canonical = _resolve_raw_instructor(str(raw), instructors_by_slug)
        if canonical is None:
            unresolved.append(str(raw).strip())
            resolved.append(slugify(str(raw)))
        else:
            resolved.append(canonical)
    return resolved, unresolved


def _build_source_slug(source: WcsSource, canonical_instructors: list[str]) -> str:
    date_prefix = (source.session_date or source.created_at.date()).isoformat()
    if source.title and source.title.strip():
        rest = slugify(source.title)
        if rest:
            return f"{date_prefix}-{rest}"
    primary = sorted(canonical_instructors)[0] if canonical_instructors else "unknown"
    return f"{date_prefix}-{primary}-{_session_type_slug(source.session_type)}"


def _raw_name_matches(raw: str, required: str) -> bool:
    raw_l = raw.strip().lower()
    req_l = required.strip().lower()
    if not raw_l or not req_l:
        return False
    if raw_l == req_l:
        return True
    if req_l in raw_l.split():
        return True
    return req_l in slugify(raw)


def _source_matches_view(source: WcsSource, spec: _ViewSpec) -> bool:
    for req in spec.instructors_includes:
        if not any(_raw_name_matches(raw, req) for raw in source.instructors_raw):
            return False
    for req in spec.students_includes:
        if not any(_raw_name_matches(raw, req) for raw in source.students_raw):
            return False
    return True


def build_indexes(export: WcsWikiExport) -> ExportIndexes:
    entities_by_id = {e.id: e for e in export.entities}
    entities_by_slug = {e.slug: e for e in export.entities}
    instructors_by_id = {i.id: i for i in export.instructors}
    instructors_by_slug = {i.slug: i for i in export.instructors}
    sources_by_id = {s.id: s for s in export.sources}

    canonical_instructors_by_source: dict[uuid.UUID, list[str]] = {}
    source_slug_by_id: dict[uuid.UUID, str] = {}
    unresolved_instructors: list[str] = []
    slug_collisions: list[tuple[str, str]] = []
    slug_owner: dict[str, uuid.UUID] = {}
    for source in export.sources:
        canonical, unresolved = _resolve_instructors_raw(
            source.instructors_raw, instructors_by_slug
        )
        unresolved_instructors.extend(unresolved)
        canonical_instructors_by_source[source.id] = canonical
        slug = _build_source_slug(source, canonical)
        # The bundle is keyed by path, so a repeated slug is not a
        # near-miss — the second render overwrites the first and one
        # source has no page at all. index.md then lists the link twice,
        # and source_count still counts both, because it is derived from
        # len(export.sources) rather than from what was written.
        if slug in slug_owner and slug_owner[slug] != source.id:
            slug_collisions.append((slug, f"{slug_owner[slug]} and {source.id}"))
        else:
            slug_owner[slug] = source.id
        source_slug_by_id[source.id] = slug

    attributions_by_entity: dict[uuid.UUID, list[WcsAttribution]] = defaultdict(list)
    attributions_by_source: dict[uuid.UUID, list[WcsAttribution]] = defaultdict(list)
    for attr in sorted(
        export.attributions, key=lambda a: (a.entity_id, a.position, str(a.id))
    ):
        attributions_by_entity[attr.entity_id].append(attr)
        attributions_by_source[attr.source_id].append(attr)

    definitions_by_entity: dict[uuid.UUID, list[WcsDefinition]] = defaultdict(list)
    definitions_by_source: dict[uuid.UUID, list[WcsDefinition]] = defaultdict(list)
    for definition in sorted(
        export.definitions, key=lambda d: (d.entity_id, d.position, str(d.id))
    ):
        definitions_by_entity[definition.entity_id].append(definition)
        definitions_by_source[definition.source_id].append(definition)

    relations_by_entity: dict[uuid.UUID, list[WcsRelation]] = defaultdict(list)
    for relation in sorted(
        export.relations, key=lambda r: (str(r.from_entity_id), str(r.id))
    ):
        relations_by_entity[relation.from_entity_id].append(relation)
        relations_by_entity[relation.to_entity_id].append(relation)

    drill_purposes_by_entity: dict[uuid.UUID, list[WcsDrillPurpose]] = defaultdict(list)
    for purpose in sorted(
        export.drill_purposes, key=lambda p: (p.drill_entity_id, str(p.id))
    ):
        drill_purposes_by_entity[purpose.drill_entity_id].append(purpose)

    technique_requirements_by_entity: dict[uuid.UUID, list[WcsTechniqueRequirement]] = (
        defaultdict(list)
    )
    for requirement in sorted(
        export.technique_requirements,
        key=lambda r: (r.technique_entity_id, str(r.id)),
    ):
        technique_requirements_by_entity[requirement.technique_entity_id].append(
            requirement
        )

    references_by_source: dict[uuid.UUID, list[WcsReference]] = defaultdict(list)
    for reference in sorted(export.references, key=lambda r: (r.source_id, str(r.id))):
        references_by_source[reference.source_id].append(reference)

    relations_by_source: dict[uuid.UUID, list[WcsRelation]] = defaultdict(list)
    for relation in sorted(
        export.relations, key=lambda r: (str(r.source_id or ""), str(r.id))
    ):
        if relation.source_id is not None:
            relations_by_source[relation.source_id].append(relation)

    return ExportIndexes(
        entities_by_id=entities_by_id,
        entities_by_slug=entities_by_slug,
        instructors_by_id=instructors_by_id,
        instructors_by_slug=instructors_by_slug,
        sources_by_id=sources_by_id,
        source_slug_by_id=source_slug_by_id,
        attributions_by_entity=dict(attributions_by_entity),
        attributions_by_source=dict(attributions_by_source),
        definitions_by_entity=dict(definitions_by_entity),
        definitions_by_source=dict(definitions_by_source),
        relations_by_entity=dict(relations_by_entity),
        drill_purposes_by_entity=dict(drill_purposes_by_entity),
        technique_requirements_by_entity=dict(technique_requirements_by_entity),
        references_by_source=dict(references_by_source),
        relations_by_source=dict(relations_by_source),
        canonical_instructors_by_source=canonical_instructors_by_source,
        unresolved_instructors=unresolved_instructors,
        slug_collisions=slug_collisions,
    )


def _instructor_heading(
    instructor_id: uuid.UUID | None,
    source: WcsSource | None,
    indexes: ExportIndexes,
) -> str:
    if instructor_id is not None:
        instructor = indexes.instructors_by_id.get(instructor_id)
        if instructor is not None:
            return instructor.canonical_name
    if source is not None and source.instructors_raw:
        return ", ".join(source.instructors_raw)
    return "Unknown"


def _source_link(source_id: uuid.UUID, indexes: ExportIndexes) -> str:
    slug = indexes.source_slug_by_id[source_id]
    return f"[[sources/{slug}]]"


def _entity_link(entity: WcsEntity, *, label: str | None = None) -> str:
    path = _entity_path(entity).removesuffix(".md")
    if label and label != entity.slug:
        return f"[[{path}|{label}]]"
    return f"[[{path}]]"


def _group_by_instructor(
    *,
    attributions: list[WcsAttribution],
    definitions: list[WcsDefinition],
    indexes: ExportIndexes,
) -> dict[str, list[tuple[str, list[str]]]]:
    """Return heading → list of (kind, rendered lines)."""
    grouped: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)

    for attr in attributions:
        if attr.attribution_kind in _MISTAKE_KINDS:
            continue
        source = indexes.sources_by_id.get(attr.source_id)
        heading = _instructor_heading(attr.instructor_id, source, indexes)
        entity = indexes.entities_by_id.get(attr.entity_id)
        lines: list[str] = []
        term = attr.raw_term.strip() or (entity.canonical_name if entity else "")
        if term:
            if entity is not None:
                lines.append(f"**{term}** — {_entity_link(entity, label=term)}")
            else:
                lines.append(f"**{term}**")
        if attr.prose.strip():
            lines.append(attr.prose.strip())
        if attr.drill_goal and attr.drill_goal.strip():
            lines.append(f"_Goal:_ {attr.drill_goal.strip()}")
        if attr.drill_steps:
            for step in attr.drill_steps:
                if step.strip():
                    lines.append(f"- {step.strip()}")
        if lines:
            lines.append(f"Source: {_source_link(attr.source_id, indexes)}")
            grouped[heading].append(("attribution", lines))

    for definition in definitions:
        source = indexes.sources_by_id.get(definition.source_id)
        heading = _instructor_heading(definition.instructor_id, source, indexes)
        lines = []
        if definition.term.strip():
            lines.append(f"**{definition.term.strip()}**")
        if definition.definition.strip():
            lines.append(definition.definition.strip())
        if lines:
            lines.append(f"Source: {_source_link(definition.source_id, indexes)}")
            grouped[heading].append(("definition", lines))

    return dict(grouped)


def _render_mistake_lines(
    attributions: list[WcsAttribution], indexes: ExportIndexes
) -> list[str]:
    lines: list[str] = []
    for attr in attributions:
        if attr.attribution_kind not in _MISTAKE_KINDS:
            continue
        mistake = (attr.mistake_text or attr.prose or "").strip()
        correction = (attr.correction_text or "").strip()
        if mistake and correction:
            text = f"{mistake} → {correction}"
        elif mistake:
            text = mistake
        elif correction:
            text = correction
        else:
            continue
        source = indexes.sources_by_id.get(attr.source_id)
        instructor = _instructor_heading(attr.instructor_id, source, indexes)
        lines.append(
            f"- {text} ({instructor}, {_source_link(attr.source_id, indexes)})"
        )
    return lines


def _collect_entity_teachers(entity_id: uuid.UUID, indexes: ExportIndexes) -> list[str]:
    slugs: set[str] = set()
    for attr in indexes.attributions_by_entity.get(entity_id, []):
        if attr.instructor_id is not None:
            instructor = indexes.instructors_by_id.get(attr.instructor_id)
            if instructor is not None:
                slugs.add(instructor.slug)
        else:
            source = indexes.sources_by_id.get(attr.source_id)
            if source is not None:
                slugs.update(indexes.canonical_instructors_by_source.get(source.id, []))
    for definition in indexes.definitions_by_entity.get(entity_id, []):
        if definition.instructor_id is not None:
            instructor = indexes.instructors_by_id.get(definition.instructor_id)
            if instructor is not None:
                slugs.add(instructor.slug)
        else:
            source = indexes.sources_by_id.get(definition.source_id)
            if source is not None:
                slugs.update(indexes.canonical_instructors_by_source.get(source.id, []))
    return sorted(slugs)


def _collect_entity_sources(entity_id: uuid.UUID, indexes: ExportIndexes) -> list[str]:
    source_ids: set[uuid.UUID] = set()
    for attr in indexes.attributions_by_entity.get(entity_id, []):
        source_ids.add(attr.source_id)
    for definition in indexes.definitions_by_entity.get(entity_id, []):
        source_ids.add(definition.source_id)
    return sorted(
        indexes.source_slug_by_id[sid]
        for sid in source_ids
        if sid in indexes.source_slug_by_id
    )


def _collect_related_slugs(entity_id: uuid.UUID, indexes: ExportIndexes) -> list[str]:
    related: set[str] = set()
    for relation in indexes.relations_by_entity.get(entity_id, []):
        other_id = (
            relation.to_entity_id
            if relation.from_entity_id == entity_id
            else relation.from_entity_id
        )
        entity = indexes.entities_by_id.get(other_id)
        if entity is not None:
            related.add(entity.slug)
    return sorted(related)


def render_entity_page(
    entity: WcsEntity,
    indexes: ExportIndexes,
    *,
    rendered_at: dt.date,
) -> str:
    attributions = indexes.attributions_by_entity.get(entity.id, [])
    definitions = indexes.definitions_by_entity.get(entity.id, [])
    relations = indexes.relations_by_entity.get(entity.id, [])
    drill_purposes = indexes.drill_purposes_by_entity.get(entity.id, [])
    requirements = indexes.technique_requirements_by_entity.get(entity.id, [])

    page = md.Page(
        frontmatter={
            "type": _entity_page_type(entity.kind),
            "slug": entity.slug,
            "canonical_name": entity.canonical_name,
            "kind": entity.kind,
            "aliases": list(entity.aliases),
            "status": entity.status,
            "teachers": _collect_entity_teachers(entity.id, indexes),
            "sources": _collect_entity_sources(entity.id, indexes),
            "related": _collect_related_slugs(entity.id, indexes),
            "rendered_at": rendered_at.isoformat(),
        }
    )

    if entity.overview_md.strip():
        page.set_section("Overview", [entity.overview_md.strip()])

    by_teacher = _group_by_instructor(
        attributions=attributions,
        definitions=definitions,
        indexes=indexes,
    )
    if by_teacher:
        body: list[str] = []
        for heading in sorted(by_teacher):
            body.append(f"### {heading}")
            for _, block_lines in by_teacher[heading]:
                body.extend(block_lines)
                body.append("")
        while body and not body[-1].strip():
            body.pop()
        page.set_section("By teacher", body)

    mistakes = _render_mistake_lines(attributions, indexes)
    if mistakes:
        page.set_section("Common mistakes", mistakes)

    if entity.kind == "drill" and drill_purposes:
        develops: list[str] = []
        for purpose in drill_purposes:
            parts = [f"**{purpose.skill_name}**"]
            if purpose.prose.strip():
                parts.append(purpose.prose.strip())
            if purpose.focus_context.strip():
                parts.append(f"_Focus:_ {purpose.focus_context.strip()}")
            develops.append(" — ".join(parts))
        page.set_section("Develops", develops)

    if entity.kind == "technique" and requirements:
        requires: list[str] = []
        for req in requirements:
            parts = [f"**{req.skill_name}**"]
            if req.prose.strip():
                parts.append(req.prose.strip())
            requires.append(" — ".join(parts))
        page.set_section("Requires", requires)

    if relations:
        related_lines: list[str] = []
        for relation in relations:
            if relation.from_entity_id == entity.id:
                other = indexes.entities_by_id.get(relation.to_entity_id)
            else:
                other = indexes.entities_by_id.get(relation.from_entity_id)
            if other is None:
                continue
            label = relation.relation_kind.strip() or "related"
            line = f"- {_entity_link(other)} ({label})"
            if relation.prose.strip():
                line += f": {relation.prose.strip()}"
            related_lines.append(line)
        if related_lines:
            page.set_section("Related", related_lines)

    return md.serialize(page)


def render_source_page(
    source: WcsSource,
    indexes: ExportIndexes,
    *,
    rendered_at: dt.date,
) -> str:
    canonical = indexes.canonical_instructors_by_source[source.id]

    page = md.Page(
        frontmatter={
            "type": "source",
            "source_id": str(source.id),
            "transcript_id": str(source.transcript_id),
            "session_type": source.session_type,
            "instructors": list(canonical),
            "instructors_raw": list(source.instructors_raw),
            "students_raw": list(source.students_raw),
            "organization": source.organization or "",
            "session_date": (
                source.session_date.isoformat() if source.session_date else None
            ),
            "title": source.title,
            "is_default_visible": bool(source.is_default_visible),
            "visibility": source.visibility,
            "rendered_at": rendered_at.isoformat(),
        }
    )

    meta_lines = [
        f"- **Date:** {source.session_date.isoformat() if source.session_date else '—'}",
        f"- **Type:** {source.session_type.replace('_', ' ')}",
        f"- **Instructors:** {', '.join(source.instructors_raw) or '—'}",
        f"- **Students:** {', '.join(source.students_raw) or '—'}",
        f"- **Organization:** {source.organization or '—'}",
    ]
    page.set_section("Metadata", meta_lines)

    taught: list[str] = []
    for attr in indexes.attributions_by_source.get(source.id, []):
        if attr.attribution_kind not in _TAUGHT_KINDS:
            continue
        entity = indexes.entities_by_id.get(attr.entity_id)
        term = attr.raw_term.strip() or (entity.canonical_name if entity else "")
        block: list[str] = []
        if term:
            if entity is not None:
                block.append(f"**{term}** — {_entity_link(entity, label=term)}")
            else:
                block.append(f"**{term}**")
        if attr.prose.strip():
            block.append(attr.prose.strip())
        if attr.drill_goal and attr.drill_goal.strip():
            block.append(f"_Goal:_ {attr.drill_goal.strip()}")
        if attr.drill_steps:
            for step in attr.drill_steps:
                if step.strip():
                    block.append(f"- {step.strip()}")
        if block:
            taught.extend(block)
            taught.append("")
    while taught and not taught[-1].strip():
        taught.pop()
    if taught:
        page.set_section("Taught", taught)

    definitions = indexes.definitions_by_source.get(source.id, [])
    if definitions:
        def_lines: list[str] = []
        for definition in definitions:
            if definition.term.strip():
                def_lines.append(f"**{definition.term.strip()}**")
            if definition.definition.strip():
                def_lines.append(definition.definition.strip())
            entity = indexes.entities_by_id.get(definition.entity_id)
            if entity is not None:
                def_lines.append(_entity_link(entity))
            def_lines.append("")
        while def_lines and not def_lines[-1].strip():
            def_lines.pop()
        page.set_section("Definitions", def_lines)

    mistakes = _render_mistake_lines(
        indexes.attributions_by_source.get(source.id, []), indexes
    )
    if mistakes:
        page.set_section("Common mistakes", mistakes)

    relation_lines: list[str] = []
    for relation in indexes.relations_by_source.get(source.id, []):
        from_entity = indexes.entities_by_id.get(relation.from_entity_id)
        to_entity = indexes.entities_by_id.get(relation.to_entity_id)
        if from_entity is None or to_entity is None:
            continue
        kind = relation.relation_kind.strip() or "related"
        line = f"- {_entity_link(from_entity)} → {_entity_link(to_entity)} ({kind})"
        if relation.prose.strip():
            line += f": {relation.prose.strip()}"
        relation_lines.append(line)
    if relation_lines:
        page.set_section("Relations", relation_lines)

    references = indexes.references_by_source.get(source.id, [])
    if references:
        ref_lines: list[str] = []
        for reference in references:
            name = reference.referenced_name.strip() or "Unknown"
            ref_type = reference.ref_type.strip()
            context = reference.context.strip()
            label = f"**{name}**"
            if ref_type:
                label += f" ({ref_type})"
            if context:
                label += f" — {context}"
            ref_lines.append(f"- {label}")
        page.set_section("References", ref_lines)

    return md.serialize(page)


def _instructor_source_ids(
    instructor: WcsInstructor, indexes: ExportIndexes
) -> list[uuid.UUID]:
    source_ids: set[uuid.UUID] = set()
    for source in indexes.sources_by_id.values():
        canonical = indexes.canonical_instructors_by_source.get(source.id, [])
        if instructor.slug in canonical:
            source_ids.add(source.id)
    return sorted(
        source_ids,
        key=lambda sid: (
            indexes.sources_by_id[sid].session_date or dt.date.min,
            indexes.source_slug_by_id.get(sid, ""),
        ),
    )


def _instructor_entities_by_kind(
    instructor: WcsInstructor, indexes: ExportIndexes
) -> dict[str, list[WcsEntity]]:
    by_kind: dict[str, list[WcsEntity]] = defaultdict(list)
    seen: set[uuid.UUID] = set()

    for attr in export_attributions_for_instructor(instructor, indexes):
        entity = indexes.entities_by_id.get(attr.entity_id)
        if entity is None or entity.id in seen:
            continue
        seen.add(entity.id)
        by_kind[_entity_page_type(entity.kind)].append(entity)

    for definition in export_definitions_for_instructor(instructor, indexes):
        entity = indexes.entities_by_id.get(definition.entity_id)
        if entity is None or entity.id in seen:
            continue
        seen.add(entity.id)
        by_kind[_entity_page_type(entity.kind)].append(entity)

    for kind in by_kind:
        by_kind[kind] = sorted(by_kind[kind], key=lambda e: e.slug)
    return dict(by_kind)


def export_attributions_for_instructor(
    instructor: WcsInstructor, indexes: ExportIndexes
) -> list[WcsAttribution]:
    """Return all attributions where this instructor is credited.

    An instructor is credited when EITHER (a) the row's instructor_id
    matches them — typical for operator-added rows — OR (b) the row's
    instructor_id is NULL and the parent source's instructors_raw
    names them. The NULL case covers all extraction-origin rows
    post-composer-fix; instructor identity for extraction content is
    derived from the source, not stored on the row.
    """
    result: list[WcsAttribution] = []
    for attrs in indexes.attributions_by_entity.values():
        for attr in attrs:
            if attr.instructor_id == instructor.id:
                result.append(attr)
                continue
            if attr.instructor_id is None:
                source = indexes.sources_by_id.get(attr.source_id)
                if source is None:
                    continue
                canonical = indexes.canonical_instructors_by_source.get(source.id, [])
                if instructor.slug in canonical:
                    result.append(attr)
    return result


def export_definitions_for_instructor(
    instructor: WcsInstructor, indexes: ExportIndexes
) -> list[WcsDefinition]:
    """Return all definitions where this instructor is credited (same
    rules as export_attributions_for_instructor)."""
    result: list[WcsDefinition] = []
    for defs in indexes.definitions_by_entity.values():
        for definition in defs:
            if definition.instructor_id == instructor.id:
                result.append(definition)
                continue
            if definition.instructor_id is None:
                source = indexes.sources_by_id.get(definition.source_id)
                if source is None:
                    continue
                canonical = indexes.canonical_instructors_by_source.get(source.id, [])
                if instructor.slug in canonical:
                    result.append(definition)
    return result


def render_instructor_page(
    instructor: WcsInstructor,
    indexes: ExportIndexes,
    *,
    rendered_at: dt.date,
) -> str:
    entities_by_kind = _instructor_entities_by_kind(instructor, indexes)
    concepts = [e.slug for e in entities_by_kind.get("concept", [])]
    techniques = [e.slug for e in entities_by_kind.get("technique", [])]
    source_ids = _instructor_source_ids(instructor, indexes)

    page = md.Page(
        frontmatter={
            "type": "instructor",
            "slug": instructor.slug,
            "canonical_name": instructor.canonical_name,
            "aliases": list(instructor.aliases),
            "sources_count": len(source_ids),
            "concepts_taught": concepts,
            "techniques_taught": techniques,
            "rendered_at": rendered_at.isoformat(),
        }
    )

    if instructor.background_md.strip():
        page.set_section("Background", [instructor.background_md.strip()])
    if instructor.teaching_themes_md.strip():
        page.set_section("Teaching themes", [instructor.teaching_themes_md.strip()])
    if instructor.notable_framings_md.strip():
        page.set_section("Notable framings", [instructor.notable_framings_md.strip()])

    teaching_lines: list[str] = []
    for kind_label, entities in sorted(entities_by_kind.items()):
        if not entities:
            continue
        teaching_lines.append(f"### {kind_label.title()}s")
        for entity in entities:
            teaching_lines.append(f"- {_entity_link(entity)}")
        teaching_lines.append("")
    while teaching_lines and not teaching_lines[-1].strip():
        teaching_lines.pop()
    if teaching_lines:
        page.set_section("Teaching", teaching_lines)

    if source_ids:
        source_lines: list[str] = []
        for source_id in source_ids:
            slug = indexes.source_slug_by_id[source_id]
            source = indexes.sources_by_id[source_id]
            title = source.title or slug
            source_lines.append(f"- [[sources/{slug}|{title}]]")
        page.set_section("Sources", source_lines)

    return md.serialize(page)


def render_view_page(
    spec: _ViewSpec,
    export: WcsWikiExport,
    indexes: ExportIndexes,
    *,
    rendered_at: dt.date,
) -> str:
    matched = [
        source
        for source in sorted(
            export.sources,
            key=lambda s: (
                s.session_date or dt.date.min,
                indexes.source_slug_by_id.get(s.id, ""),
            ),
        )
        if spec.slug == "full-model" or _source_matches_view(source, spec)
    ]

    page = md.Page(
        frontmatter={
            "type": "view",
            "slug": spec.slug,
            "filter": spec.filter_description,
            "rendered_at": rendered_at.isoformat(),
        }
    )

    if not matched:
        page.preamble = [
            "_No sources currently match this view._",
        ]
        return md.serialize(page)

    page.preamble = [f"_{len(matched)} matching sources._"]

    source_lines: list[str] = []
    concept_slugs: set[str] = set()
    technique_slugs: set[str] = set()
    pattern_slugs: set[str] = set()
    for source in matched:
        slug = indexes.source_slug_by_id[source.id]
        date_str = source.session_date.isoformat() if source.session_date else "—"
        title = source.title or slug
        who = ", ".join(source.instructors_raw) or "(unknown instructor)"
        students = f" → {', '.join(source.students_raw)}" if source.students_raw else ""
        source_lines.append(
            f"- **{date_str}** — [[sources/{slug}|{title}]] · {who}{students}"
        )
        for attr in indexes.attributions_by_source.get(source.id, []):
            entity = indexes.entities_by_id.get(attr.entity_id)
            if entity is None:
                continue
            if entity.kind == "concept":
                concept_slugs.add(entity.slug)
            elif entity.kind == "technique":
                technique_slugs.add(entity.slug)
            elif entity.kind == "pattern":
                pattern_slugs.add(entity.slug)
    page.set_section("Sources", source_lines)

    if concept_slugs:
        page.set_section(
            "Concepts touched",
            [f"- [[concepts/{slug}]]" for slug in sorted(concept_slugs)],
        )
    if technique_slugs:
        page.set_section(
            "Techniques touched",
            [f"- [[techniques/{slug}]]" for slug in sorted(technique_slugs)],
        )
    if pattern_slugs:
        page.set_section(
            "Patterns touched",
            [f"- [[patterns/{slug}]]" for slug in sorted(pattern_slugs)],
        )

    return md.serialize(page)


def render_index(
    export: WcsWikiExport,
    indexes: ExportIndexes,
    *,
    rendered_at: dt.date,
) -> str:
    lines = [
        "# WCS Wiki Index",
        "",
        f"_Regenerated {rendered_at.isoformat()}._",
        "",
    ]

    def section(title: str, entries: list[tuple[str, str]]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not entries:
            lines.append(f"_No {title.lower()} yet._")
        else:
            for link, descriptor in sorted(entries, key=lambda x: x[0].lower()):
                lines.append(f"- [[{link}]] — {descriptor}")
        lines.append("")

    concepts = [
        (f"concepts/{e.slug}", e.canonical_name)
        for e in export.entities
        if e.kind == "concept"
    ]
    techniques = [
        (f"techniques/{e.slug}", e.canonical_name)
        for e in export.entities
        if e.kind == "technique"
    ]
    patterns = [
        (f"patterns/{e.slug}", e.canonical_name)
        for e in export.entities
        if e.kind == "pattern"
    ]
    drills = [
        (f"drills/{e.slug}", e.canonical_name)
        for e in export.entities
        if e.kind == "drill"
    ]
    instructors = [
        (f"instructors/{i.slug}", i.canonical_name) for i in export.instructors
    ]
    sources = []
    for source in export.sources:
        slug = indexes.source_slug_by_id[source.id]
        descriptor = source.title or slug
        sources.append((f"sources/{slug}", descriptor))
    views = [(f"views/{spec.slug}", spec.filter_description) for spec in REQUIRED_VIEWS]

    section("Concepts", concepts)
    section("Techniques", techniques)
    section("Patterns", patterns)
    section("Drills", drills)
    section("Instructors", instructors)
    section("Sources", sources)
    section("Views", views)

    return "\n".join(lines).rstrip() + "\n"


def format_log_entry(stats: RenderStats, *, rendered_at: dt.date) -> str:
    summary = (
        f"Rendered {stats.entity_count} entities "
        f"({stats.concept_count} concepts, {stats.technique_count} techniques, "
        f"{stats.pattern_count} patterns, {stats.drill_count} drills), "
        f"{stats.instructor_count} instructors, "
        f"{stats.source_count} sources."
    )
    if stats.observations:
        summary += " " + " ".join(stats.observations)
    return f"\n## [{rendered_at.isoformat()}] render | export\n\n{summary}\n"


def append_log_entry(existing_log: str, entry: str) -> str:
    if not existing_log.strip():
        return "# Render log\n" + entry
    return existing_log.rstrip() + entry


def find_dangling_references(
    export: WcsWikiExport, indexes: ExportIndexes
) -> list[tuple[str, str]]:
    """Every id in the export that resolves to nothing.

    Computed from the data rather than observed during rendering. The
    renderers skip an unresolvable reference quietly and correctly — a
    page cannot link to an entity it does not have — but they do it in
    seven places, several of which are ordinary filters rather than
    drops, and one dangling entity can be skipped on three different
    pages. Counting at the skip sites would both miss and double-count.
    One pass answers the question directly.

    Returns ``(reason, reference)`` pairs, so the caller can report them
    without this module knowing anything about notifications.
    """
    dangling: list[tuple[str, str]] = []
    entities = indexes.entities_by_id
    sources = indexes.sources_by_id

    for relation in export.relations:
        if relation.from_entity_id not in entities:
            dangling.append(("missing_entity", f"relation {relation.id} from"))
        if relation.to_entity_id not in entities:
            dangling.append(("missing_entity", f"relation {relation.id} to"))

    for attr in export.attributions:
        if attr.entity_id not in entities:
            dangling.append(("missing_entity", f"attribution {attr.id}"))
        if attr.source_id not in sources:
            dangling.append(("missing_source", f"attribution {attr.id}"))

    for definition in export.definitions:
        if definition.entity_id not in entities:
            dangling.append(("missing_entity", f"definition {definition.id}"))
        if definition.source_id not in sources:
            dangling.append(("missing_source", f"definition {definition.id}"))

    return dangling


def render_bundle(
    export: WcsWikiExport,
    *,
    rendered_at: dt.date | None = None,
    existing_log: str = "",
) -> tuple[dict[str, str], RenderStats]:
    """Render the full wiki bundle. Keys are repo-relative paths."""
    when = rendered_at or dt.date.today()
    indexes = build_indexes(export)
    bundle: dict[str, str] = {}

    stats = RenderStats(
        entity_count=len(export.entities),
        concept_count=sum(1 for e in export.entities if e.kind == "concept"),
        technique_count=sum(1 for e in export.entities if e.kind == "technique"),
        pattern_count=sum(1 for e in export.entities if e.kind == "pattern"),
        drill_count=sum(1 for e in export.entities if e.kind == "drill"),
        instructor_count=len(export.instructors),
        source_count=len(export.sources),
    )

    stats.dropped.extend(find_dangling_references(export, indexes))
    stats.dropped.extend(
        ("unresolved_instructor", name) for name in indexes.unresolved_instructors
    )
    stats.dropped.extend(
        ("source_slug_collision", f"{slug} ({who})")
        for slug, who in indexes.slug_collisions
    )

    for entity in sorted(export.entities, key=lambda e: e.slug):
        path = _entity_path(entity)
        bundle[path] = render_entity_page(entity, indexes, rendered_at=when)
        if not indexes.attributions_by_entity.get(entity.id):
            stats.observations.append(f"Entity `{entity.slug}` has no attributions.")

    for source in sorted(export.sources, key=lambda s: indexes.source_slug_by_id[s.id]):
        slug = indexes.source_slug_by_id[source.id]
        bundle[f"sources/{slug}.md"] = render_source_page(
            source, indexes, rendered_at=when
        )

    for instructor in sorted(export.instructors, key=lambda i: i.slug):
        path = f"instructors/{instructor.slug}.md"
        bundle[path] = render_instructor_page(instructor, indexes, rendered_at=when)

    for spec in REQUIRED_VIEWS:
        path = f"views/{spec.slug}.md"
        bundle[path] = render_view_page(spec, export, indexes, rendered_at=when)

    bundle["index.md"] = render_index(export, indexes, rendered_at=when)
    bundle["log.md"] = append_log_entry(
        existing_log, format_log_entry(stats, rendered_at=when)
    )

    return bundle, stats


DERIVED_GLOBS: tuple[str, ...] = (
    "concepts/*.md",
    "techniques/*.md",
    "patterns/*.md",
    "drills/*.md",
    "instructors/*.md",
    "sources/*.md",
    "views/*.md",
)

PROTECTED_ROOT_FILES: frozenset[str] = frozenset({"CLAUDE.md", "README.md", "log.md"})


def list_stale_derived_paths(
    wiki_repo_path: Any, expected_paths: set[str]
) -> list[Any]:
    """Return absolute paths for derived pages not in ``expected_paths``."""
    from pathlib import Path

    root = Path(wiki_repo_path)
    stale: list[Path] = []
    for pattern in DERIVED_GLOBS:
        for path in root.glob(pattern):
            rel = path.relative_to(root).as_posix()
            if rel not in expected_paths:
                stale.append(path)
    return stale
