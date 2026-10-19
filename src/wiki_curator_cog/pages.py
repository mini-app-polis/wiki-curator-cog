"""Page documents: each entity page as structured data (ADR-006, MINI-96).

The curator owns the page logic — folds, the reference overlay, By teacher.
:func:`page_document` runs it for one entity and returns the page as a
``WcsWikiPage``: definition, In practice, Details, statements grouped by
teacher with their citations, Mentions, Referenced by. The ``materialise``
job stores these in the API, which filters each reader's view by the sources
they can see and serves them; it never recomputes a page.

The markdown export is a reader of the same documents. :func:`page_markdown`
writes the page the wcs-wiki repo has always had, from the document alone, so
there is one page model with two readers (MINI-99).

Text fields are markdown, with neutral links: ``[[slug]]`` or
``[[slug|label]]`` for a page and ``[[source:<uuid>|label]]`` for a source.
Each reader turns them into its own URLs; :func:`page_markdown` turns them
back into the repo's paths, so the export is byte for byte what it was.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from mini_app_polis.api.contract import (
    WcsWikiPage,
    WcsWikiPageCitation,
    WcsWikiPageDetail,
    WcsWikiPageLink,
    WcsWikiPageMistake,
    WcsWikiPageRelated,
    WcsWikiPageSkill,
    WcsWikiPageStatement,
    WcsWikiPageTeacher,
)

from . import markdown_utils as md
from . import render as _render
from .render import (
    _MISTAKE_KINDS,
    ExportIndexes,
    LinkOverlay,
    WcsEntity,
    WcsWikiExport,
    _collect_entity_teachers,
    _entity_directory,
    _entity_page_type,
    _instructor_heading,
    build_indexes,
    slugify,
)

_ENTITY_DIRS = frozenset({"concepts", "techniques", "patterns", "drills"})
_SOURCE_PREFIX = "source:"
_WIKI_LINK = re.compile(r"\[\[([^\]|#]+)((?:#[^\]|]*)?)(?:\|([^\]]*))?\]\]")


def _relink(m: re.Match[str], target: str) -> str:
    anchor, label = m.group(2) or "", m.group(3)
    return (
        f"[[{target}{anchor}|{label}]]"
        if label is not None
        else f"[[{target}{anchor}]]"
    )


# ── Neutral links ──


def neutral_links(
    text: str, paths: dict[str, str], source_ids: dict[str, uuid.UUID]
) -> str:
    """Links to pages and sources in ``text`` made neutral; anything else untouched.

    Only a link to a page that exists, at the path it has, is rewritten, so
    :func:`path_links` gives back exactly the text it was given.
    """

    def fix(m: re.Match[str]) -> str:
        target = m.group(1)
        folder, _, slug = target.partition("/")
        if folder in _ENTITY_DIRS and paths.get(slug) == target:
            return _relink(m, slug)
        if folder == "sources" and slug in source_ids:
            return _relink(m, f"{_SOURCE_PREFIX}{source_ids[slug]}")
        return m.group(0)

    return _WIKI_LINK.sub(fix, text)


def path_links(
    text: str, paths: dict[str, str], source_slugs: dict[uuid.UUID, str]
) -> str:
    """Neutral links in ``text`` as repo paths; the inverse of :func:`neutral_links`."""

    def fix(m: re.Match[str]) -> str:
        target = m.group(1)
        if target.startswith(_SOURCE_PREFIX):
            try:
                slug = source_slugs.get(uuid.UUID(target[len(_SOURCE_PREFIX) :]))
            except ValueError:
                slug = None
            return _relink(m, f"sources/{slug}") if slug else m.group(0)
        if target in paths:
            return _relink(m, paths[target])
        return m.group(0)

    return _WIKI_LINK.sub(fix, text)


def _path(kind: str, slug: str) -> str:
    return f"{_entity_directory(kind)}/{slug}"


# ── Building a document ──


def _citation(source_id: uuid.UUID, indexes: ExportIndexes) -> WcsWikiPageCitation:
    source = indexes.sources_by_id.get(source_id)
    return WcsWikiPageCitation(
        source_id=source_id,
        source_slug=indexes.source_slug_by_id.get(source_id, ""),
        session_date=source.session_date if source is not None else None,
        public=bool(source.is_default_visible) if source is not None else False,
    )


def _link(entity: WcsEntity, count: int) -> WcsWikiPageLink:
    return WcsWikiPageLink(
        slug=entity.slug, kind=entity.kind, name=entity.canonical_name, count=count
    )


def _counted(
    counts: dict[uuid.UUID, int], indexes: ExportIndexes
) -> list[WcsWikiPageLink]:
    """Most-referenced first, then by slug: the order the page lists them."""
    rows = [
        (-n, other.slug, _link(other, n))
        for other_id, n in counts.items()
        if (other := indexes.entities_by_id.get(other_id)) is not None
    ]
    return [link for _, _, link in sorted(rows, key=lambda r: (r[0], r[1]))]


def _by_teacher(entity: WcsEntity, indexes: ExportIndexes) -> list[WcsWikiPageTeacher]:
    grouped: dict[str, list[WcsWikiPageStatement]] = {}
    for attr in indexes.attributions_by_entity.get(entity.id, []):
        if attr.attribution_kind in _MISTAKE_KINDS:
            continue
        source = indexes.sources_by_id.get(attr.source_id)
        heading = _instructor_heading(attr.instructor_id, source, indexes)
        subject = indexes.entities_by_id.get(attr.entity_id)
        term = attr.raw_term.strip() or (subject.canonical_name if subject else "")
        grouped.setdefault(heading, []).append(
            WcsWikiPageStatement(
                kind="attribution",
                term=term,
                text=attr.prose.strip(),
                goal=(attr.drill_goal or "").strip(),
                steps=[s.strip() for s in attr.drill_steps or [] if s.strip()],
                citation=_citation(attr.source_id, indexes),
            )
        )
    for definition in indexes.definitions_by_entity.get(entity.id, []):
        term, text = definition.term.strip(), definition.definition.strip()
        if not term and not text:
            continue
        source = indexes.sources_by_id.get(definition.source_id)
        heading = _instructor_heading(definition.instructor_id, source, indexes)
        grouped.setdefault(heading, []).append(
            WcsWikiPageStatement(
                kind="definition",
                term=term,
                text=text,
                goal="",
                steps=[],
                citation=_citation(definition.source_id, indexes),
            )
        )
    return [
        WcsWikiPageTeacher(name=name, statements=grouped[name])
        for name in sorted(grouped)
    ]


def _mistakes(entity: WcsEntity, indexes: ExportIndexes) -> list[WcsWikiPageMistake]:
    out: list[WcsWikiPageMistake] = []
    for attr in indexes.attributions_by_entity.get(entity.id, []):
        if attr.attribution_kind not in _MISTAKE_KINDS:
            continue
        mistake = (attr.mistake_text or attr.prose or "").strip()
        correction = (attr.correction_text or "").strip()
        text = (
            f"{mistake} → {correction}"
            if mistake and correction
            else mistake or correction
        )
        if not text:
            continue
        source = indexes.sources_by_id.get(attr.source_id)
        out.append(
            WcsWikiPageMistake(
                text=text,
                teacher=_instructor_heading(attr.instructor_id, source, indexes),
                citation=_citation(attr.source_id, indexes),
            )
        )
    return out


def _related(entity: WcsEntity, indexes: ExportIndexes) -> list[WcsWikiPageRelated]:
    out: list[WcsWikiPageRelated] = []
    for relation in indexes.relations_by_entity.get(entity.id, []):
        other_id = (
            relation.to_entity_id
            if relation.from_entity_id == entity.id
            else relation.from_entity_id
        )
        other = indexes.entities_by_id.get(other_id)
        if other is None:
            continue
        sid = relation.source_id
        out.append(
            WcsWikiPageRelated(
                slug=other.slug,
                kind=other.kind,
                name=other.canonical_name,
                relation=relation.relation_kind.strip() or "related",
                text=relation.prose.strip(),
                citation=_citation(sid, indexes)
                if sid is not None and sid in indexes.sources_by_id
                else None,
            )
        )
    return out


def _sources(entity: WcsEntity, indexes: ExportIndexes) -> list[WcsWikiPageCitation]:
    ids = {a.source_id for a in indexes.attributions_by_entity.get(entity.id, [])}
    ids |= {d.source_id for d in indexes.definitions_by_entity.get(entity.id, [])}
    cited = [_citation(sid, indexes) for sid in ids if sid in indexes.source_slug_by_id]
    return sorted(cited, key=lambda c: c.source_slug)


def display_aliases(name: str, aliases: Iterable[str]) -> list[str]:
    """The names a page lists under "also called", in their stored order.

    Entities carry slug-form aliases for matching (link.py resolves targets
    through them, and identity.apply_merges adds a folded entity's names), so
    they stay on the entity. A reader needs each name once (MINI-104): this
    leaves out an alias equal to the page's name ignoring case, a repeat
    ignoring case, and one that is only the slug form of the page's name or
    of another alias listed ("lateral-push" beside "lateral push").
    """
    listed = [a.strip() for a in aliases if a and a.strip()]
    own = name.strip().casefold()
    slug_forms = {slugify(name)} | {slugify(a) for a in listed if a != slugify(a)}
    seen: set[str] = set()
    kept: list[str] = []
    for alias in listed:
        key = alias.casefold()
        if key == own or key in seen:
            continue
        if alias == slugify(alias) and alias in slug_forms:
            continue
        seen.add(key)
        kept.append(alias)
    return kept


def page_document(
    entity: WcsEntity,
    indexes: ExportIndexes,
    *,
    overlay: LinkOverlay | None = None,
    context: str = "",
    details: Sequence[tuple[Any, ...]] = (),
    detail_slugs: dict[str, str] | None = None,
    folded: Iterable[str] = (),
    evidence: Iterable[uuid.UUID] = (),
    paths: dict[str, str] | None = None,
) -> WcsWikiPage:
    """One entity's page as a document.

    ``details`` are the facets folded into it, as ``(name, gist, depth)``;
    ``detail_slugs`` names each one's own slug. ``folded`` is every slug
    folded into it, facets and same-idea names alike, so a lookup of one can
    be served this page (MINI-100). ``evidence`` is every source
    the page's prose was built from — its own statements' and those of the
    statements elsewhere that refer to it. With an ``overlay`` the page gets
    Mentions and Referenced by. ``paths`` (every page's repo path, by slug)
    is computed when not given; pass it when building many pages.
    """
    if paths is None:
        paths = {e.slug: _path(e.kind, e.slug) for e in indexes.entities_by_id.values()}
    source_ids = {slug: sid for sid, slug in indexes.source_slug_by_id.items()}

    def neutral(text: str) -> str:
        return neutral_links(text, paths, source_ids)

    relations = indexes.relations_by_entity.get(entity.id, [])

    mentions: list[WcsWikiPageLink] = []
    referenced_by: list[WcsWikiPageLink] = []
    if overlay is not None:
        mentions = _counted(overlay.mentions.get(entity.id, {}), indexes)
        incoming = dict(overlay.referenced_by.get(entity.id, {}))
        for relation in relations:
            if (
                relation.to_entity_id == entity.id
                and relation.from_entity_id != entity.id
            ):
                incoming[relation.from_entity_id] = (
                    incoming.get(relation.from_entity_id, 0) + 1
                )
        referenced_by = _counted(incoming, indexes)

    drill_purposes = indexes.drill_purposes_by_entity.get(entity.id, [])
    requirements = indexes.technique_requirements_by_entity.get(entity.id, [])
    return WcsWikiPage(
        slug=entity.slug,
        name=entity.canonical_name,
        kind=entity.kind,
        status=entity.status,
        aliases=display_aliases(entity.canonical_name, entity.aliases),
        teachers=_collect_entity_teachers(entity.id, indexes),
        definition=neutral(entity.overview_md.strip()),
        in_practice=neutral(context.strip()),
        details=[
            WcsWikiPageDetail(
                slug=(detail_slugs or {}).get(name) or slugify(name),
                name=name,
                gist=neutral(gist or ""),
                depth=rest[0] if rest else 0,
            )
            for name, gist, *rest in details
        ],
        by_teacher=_by_teacher(entity, indexes),
        mistakes=_mistakes(entity, indexes),
        develops=[
            WcsWikiPageSkill(
                skill=p.skill_name,
                text=p.prose.strip(),
                focus=p.focus_context.strip(),
                citation=_citation(p.source_id, indexes) if p.source_id else None,
            )
            for p in drill_purposes
        ]
        if entity.kind == "drill"
        else [],
        requires=[
            WcsWikiPageSkill(
                skill=r.skill_name,
                text=r.prose.strip(),
                focus="",
                citation=_citation(r.source_id, indexes) if r.source_id else None,
            )
            for r in requirements
        ]
        if entity.kind == "technique"
        else [],
        related=_related(entity, indexes),
        mentions=mentions,
        referenced_by=referenced_by,
        sources=_sources(entity, indexes),
        evidence_sources=sorted(set(evidence), key=str),
        folded=sorted(set(folded)),
    )


def build_documents(
    export: WcsWikiExport,
    *,
    overlay: LinkOverlay | None = None,
    context: dict[uuid.UUID, str] | None = None,
    details: dict[uuid.UUID, list[tuple[Any, ...]]] | None = None,
    merges: Any = None,
    evidence: dict[str, set[uuid.UUID]] | None = None,
    only: Iterable[str] | None = None,
) -> dict[str, WcsWikiPage]:
    """Every entity's page (or ``only`` these slugs), keyed by slug.

    ``export`` is the one the pages render from: folds applied, proposed
    concepts added, generated definitions in ``overview_md``.
    """
    indexes = build_indexes(export)
    paths = paths_of(export)
    wanted = set(only) if only is not None else None
    facets = getattr(merges, "facets", None) or {}
    folded: dict[str, list[str]] = {}
    for name, into in (getattr(merges, "by_slug", None) or {}).items():
        folded.setdefault(into, []).append(name)
    out: dict[str, WcsWikiPage] = {}
    for entity in sorted(export.entities, key=lambda e: e.slug):
        if wanted is not None and entity.slug not in wanted:
            continue
        out[entity.slug] = page_document(
            entity,
            indexes,
            overlay=overlay,
            context=(context or {}).get(entity.id, ""),
            details=(details or {}).get(entity.id, ()),
            detail_slugs={f.name: f.slug for f in facets.get(entity.id, [])},
            folded=folded.get(entity.slug, ()),
            evidence=(evidence or {}).get(entity.slug, set()),
            paths=paths,
        )
    return out


# ── The markdown export ──


def _source_line(citation: WcsWikiPageCitation) -> str:
    return f"[[sources/{citation.source_slug}]]"


def _statement_lines(page: WcsWikiPage, st: WcsWikiPageStatement) -> list[str]:
    lines: list[str] = []
    if st.term:
        if st.kind == "attribution":
            path = _path(page.kind, page.slug)
            link = f"[[{path}|{st.term}]]" if st.term != page.slug else f"[[{path}]]"
            lines.append(f"**{st.term}** — {link}")
        else:
            lines.append(f"**{st.term}**")
    if st.text:
        lines.append(st.text)
    if st.goal:
        lines.append(f"_Goal:_ {st.goal}")
    lines += [f"- {step}" for step in st.steps]
    lines.append(f"Source: {_source_line(st.citation)}")
    return lines


def _link_lines(links: list[WcsWikiPageLink]) -> list[str]:
    return [
        f"- [[{_path(x.kind, x.slug)}]]" + (f" ({x.count})" if x.count > 1 else "")
        for x in links
    ]


def page_markdown(
    page: WcsWikiPage,
    *,
    rendered_at: dt.date,
    paths: dict[str, str],
    source_slugs: dict[uuid.UUID, str],
    overlay: bool = True,
) -> str:
    """The wcs-wiki repo's markdown for one page document.

    ``paths`` maps every page slug to its repo path and ``source_slugs``
    every source to its page slug, for turning neutral links back into
    paths. ``overlay`` says whether Mentions and Referenced by are on: a
    render without the linking pass's references has neither section.
    """

    def text(value: str) -> str:
        return path_links(value, paths, source_slugs)

    out = md.Page(
        frontmatter={
            "type": _entity_page_type(page.kind),
            "slug": page.slug,
            "canonical_name": page.name,
            "kind": page.kind,
            "aliases": list(page.aliases),
            "status": page.status,
            "teachers": list(page.teachers),
            "sources": [c.source_slug for c in page.sources],
            "related": sorted({r.slug for r in page.related}),
        }
    )
    if page.definition:
        out.set_section("Overview", [text(page.definition)])
    if page.in_practice:
        out.set_section("In practice", [text(page.in_practice)])
    if page.details:
        lines = []
        for d in page.details:
            item = f"**{d.name}** — {text(d.gist)}" if d.gist else f"**{d.name}**"
            lines.append(f"{'  ' * d.depth}- {item}")
        out.set_section("Details", lines)

    if page.by_teacher:
        body: list[str] = []
        for teacher in page.by_teacher:
            body.append(f"### {teacher.name}")
            for st in teacher.statements:
                body.extend(_statement_lines(page, st))
                body.append("")
        while body and not body[-1].strip():
            body.pop()
        out.set_section("By teacher", body)

    if page.mistakes:
        out.set_section(
            "Common mistakes",
            [
                f"- {m.text} ({m.teacher}, {_source_line(m.citation)})"
                for m in page.mistakes
            ],
        )
    if page.develops:
        out.set_section(
            "Develops",
            [
                " — ".join(
                    [f"**{s.skill}**"]
                    + ([s.text] if s.text else [])
                    + ([f"_Focus:_ {s.focus}"] if s.focus else [])
                )
                for s in page.develops
            ],
        )
    if page.requires:
        out.set_section(
            "Requires",
            [
                " — ".join([f"**{s.skill}**"] + ([s.text] if s.text else []))
                for s in page.requires
            ],
        )
    if page.related:
        out.set_section(
            "Related",
            [
                f"- [[{_path(r.kind, r.slug)}]] ({r.relation})"
                + (f": {r.text}" if r.text else "")
                for r in page.related
            ],
        )
    if overlay:
        if page.mentions:
            out.set_section("Mentions", _link_lines(page.mentions))
        if len(page.referenced_by) > _render.REFERENCED_BY_LINK_LIMIT:
            out.set_section(
                "Referenced by", [f"Referenced by {len(page.referenced_by)} pages."]
            )
        elif page.referenced_by:
            out.set_section("Referenced by", _link_lines(page.referenced_by))
    return md.serialize(out)


def paths_of(export: WcsWikiExport) -> dict[str, str]:
    """Every entity slug's repo path, without ``.md``."""
    return {e.slug: _path(e.kind, e.slug) for e in export.entities}
