"""One unit of work per job: what the wiki-jobs worker runs (MINI-94, part 2).

ADR-006 has the API enqueue wiki work after a source is composed, one message
per unit, and a Lambda worker handle each message. This module is that
worker's body, runtime-agnostic: :func:`handle` takes one job, runs it
against a :class:`RunContext` built for that invocation, writes the result to
the state store, and returns the jobs that should follow, for the API to
enqueue.

========================  ==========================  ===========================
job                       unit                        follows with
========================  ==========================  ===========================
``link``                  one source's statements     ``consolidate`` for its
                                                      undecided entities;
                                                      ``generate`` for every
                                                      entity a newly linked
                                                      statement taught or
                                                      referred to
``consolidate``           named undecided entities    ``generate`` for each page
                                                      that gained a fold
``generate``              named entities              ``materialise`` for every
                                                      page it was asked about
``materialise``           named pages                 —
========================  ==========================  ===========================

Every unit is idempotent under its pass's watermark: a statement already
linked, an entity already decided, an entity with nothing new to integrate
costs no model call and writes nothing. ``materialise`` calls no model; it
rebuilds each page document from the state as it stands and stores it, so
repeating it rewrites the same document. So a retried or duplicated message is
a no-op, and a follow-on job that turns out to have nothing to do is cheap.

The context is explicit — no module state — and built once per invocation.
The local commands use the same pass functions over the whole corpus; these
are the same functions over one unit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log

from . import consolidate, generate, identity, link, pages, state_backend
from .render import WcsWikiExport

LOG = log.get_logger()

JOBS = ("link", "consolidate", "generate", "materialise")

#: Entities per follow-on generate job. Each is one model call at most, made
#: one after another, so this keeps a job well inside a Lambda's 15 minutes
#: while sparing the worker one export and state read per entity.
GENERATE_PER_JOB = 25

#: Pages per follow-on materialise job. No model call; the time is the
#: export and state read every invocation pays, so as many as generate.
MATERIALISE_PER_JOB = 25


class JobError(ValueError):
    """A job message this worker cannot run."""


@dataclass(frozen=True)
class StatePaths:
    """Where each state document is cached (or kept, with ``FileState``)."""

    links: Path
    facets: Path
    prose: Path
    #: Where a copy of each page document is written (MINI-96).
    pages: Path

    @classmethod
    def under(cls, folder: Path) -> StatePaths:
        """The standard state file paths inside ``folder``."""
        return cls(
            links=folder / "links.json",
            facets=folder / "facets.json",
            prose=folder / "prose.json",
            pages=folder / "pages",
        )


@dataclass
class RunContext:
    """Everything a unit reads, built once per invocation and passed in."""

    #: The export with notes' and the facets file's folds applied.
    export: WcsWikiExport
    merges: identity.Merges
    store: state_backend.FileState
    paths: StatePaths
    llm: Any
    model: str
    #: The facet decisions, read once for the invocation: the folds above
    #: come from them, and link and consolidate use the same object.
    facets: consolidate.FacetsFile
    #: A wcs-wiki clone, when there is one: generation builds on an edited
    #: page. The worker has none.
    wiki_repo_path: Path | None = None
    #: The raw export, for passes that apply the folds themselves.
    raw_export: WcsWikiExport | None = field(default=None, repr=False)


def build_context(
    export: WcsWikiExport,
    store: state_backend.FileState,
    paths: StatePaths,
    *,
    llm: Any,
    model: str,
    wiki_repo_path: Path | None = None,
) -> RunContext:
    """Apply the folds the facets state calls for, once, for this invocation."""
    facets = store.facets(paths.facets)
    assert facets is not None
    merged, merges = identity.resolve(export, facets.folds())
    return RunContext(
        export=merged,
        merges=merges,
        store=store,
        paths=paths,
        llm=llm,
        model=model,
        facets=facets,
        wiki_repo_path=wiki_repo_path,
        raw_export=export,
    )


def _job(kind: str, **fields: Any) -> dict[str, Any]:
    return {"job": kind, **fields}


def _entity_jobs(
    kind: str, slugs: set[str] | list[str], per: int
) -> list[dict[str, Any]]:
    ordered = sorted(set(slugs))
    return [
        _job(kind, entities=ordered[i : i + per]) for i in range(0, len(ordered), per)
    ]


def _generate_jobs(slugs: set[str] | list[str]) -> list[dict[str, Any]]:
    return _entity_jobs("generate", slugs, GENERATE_PER_JOB)


def _materialise_jobs(slugs: set[str] | list[str]) -> list[dict[str, Any]]:
    return _entity_jobs("materialise", slugs, MATERIALISE_PER_JOB)


# ── link ──


def link_source(ctx: RunContext, source_id: uuid.UUID | str) -> dict[str, Any]:
    """Link one source's statements; follow with consolidate and generate."""
    sid = uuid.UUID(str(source_id))
    statements = [s for s in link.collect_statements(ctx.export) if s.source_id == sid]
    links = ctx.store.links(ctx.paths.links)
    assert links is not None
    # Only what this run links sends pages to be regenerated: a repeated job
    # links nothing, so it asks for nothing.
    newly = [s for s in statements if not links.is_linked(s)]
    counts = link.run_linking(
        statements,
        link.Catalog(ctx.export.entities),
        links,
        ctx.llm,
        model=ctx.model,
        on_batch=lambda: ctx.store.save(links, ctx.paths.links),
    )
    ctx.store.save(links, ctx.paths.links, final=True)

    touched: set[str] = set()
    for s in newly:
        if not links.is_linked(s):
            continue  # its batch failed; the next run tries it again
        touched.add(s.subject_slug)
        entry = links.statements.get(s.key) or {}
        touched.update(r["target_slug"] for r in entry.get("references", []))

    facets = ctx.facets
    version_of = {k: g.version for g in consolidate.GROUPS for k in g.kinds}
    by_slug = {e.slug: e for e in ctx.export.entities}
    undecided = sorted(
        slug
        for slug in {s.subject_slug for s in statements}
        if slug in by_slug
        and by_slug[slug].kind in version_of
        and not facets.is_decided(slug, version_of[by_slug[slug].kind])
    )
    follow = ([_job("consolidate", entities=undecided)] if undecided else []) + (
        _generate_jobs(touched)
    )
    return {
        "job": "link",
        "source_id": str(sid),
        "statements": len(statements),
        **{k: counts[k] for k in ("skipped", "linked", "failed_batches")},
        "next": follow,
    }


# ── consolidate ──


def consolidate_entities(ctx: RunContext, slugs: list[str]) -> dict[str, Any]:
    """Decide these entities; follow with generate for each page that gained one.

    Run on the export with only the notes' merges applied, as the full pass
    is, so an entity is decided by its own name and evidence.
    """
    raw = ctx.raw_export if ctx.raw_export is not None else ctx.export
    export, merges = identity.resolve(raw)
    links = ctx.store.links(ctx.paths.links)
    supports = consolidate.support(export, links, merges)
    facets = ctx.facets
    before = facets.parents()
    counts = consolidate.run_consolidation(
        export,
        facets,
        ctx.llm,
        model=ctx.model,
        supports=supports,
        only=set(slugs),
        on_batch=lambda: ctx.store.save(facets, ctx.paths.facets),
    )
    ctx.store.save(facets, ctx.paths.facets, final=True)
    gained = sorted(
        {
            parent
            for slug, parent in facets.parents().items()
            if slug in slugs and before.get(slug) != parent
        }
    )
    return {
        "job": "consolidate",
        "entities": sorted(slugs),
        **{k: counts[k] for k in ("skipped", "decided", "failed_batches")},
        "next": _generate_jobs(gained),
    }


# ── generate ──


def generate_entities(ctx: RunContext, slugs: list[str]) -> dict[str, Any]:
    """Integrate these entities' pending statements. No call for one with none.

    A slug folded away since the job was enqueued is generated as the page it
    folded into; one that no longer exists at all is skipped. Every page asked
    about is materialised next, whether or not its prose changed: a new
    reference or fold changes the page without new prose.
    """
    links = ctx.store.links(ctx.paths.links)
    assert links is not None
    export, overlay, _ = link.build_overlay(ctx.export, links, ctx.merges)
    corpus = generate.build_corpus(export, overlay, links, ctx.merges)
    targets: dict[str, Any] = {}
    skipped: list[str] = []
    for slug in slugs:
        target = ctx.merges.by_slug.get(slug, slug) if ctx.merges else slug
        entity = corpus.entities.get(target)
        if entity is None:
            skipped.append(slug)
        else:
            targets.setdefault(target, entity)
    prose = ctx.store.prose(ctx.paths.prose)
    assert prose is not None
    counts = generate.run_generation(
        list(targets.values()),
        corpus,
        prose,
        ctx.llm,
        model=ctx.model,
        wiki_repo_path=ctx.wiki_repo_path,
    )
    ctx.store.save(prose, ctx.paths.prose, final=True)
    return {
        "job": "generate",
        "entities": sorted(targets),
        "skipped": sorted(skipped),
        **{k: counts[k] for k in ("generated", "failed")},
        # a page gone since the job was enqueued is removed by materialise
        "next": _materialise_jobs(set(targets) | set(skipped)),
    }


# ── materialise ──


def materialise_pages(ctx: RunContext, slugs: list[str] | None) -> dict[str, Any]:
    """Build these pages' documents from the state as it stands, and store them.

    No model call. A slug folded into another page is materialised as that
    page; every name folded into a page goes when the page is written, and a
    slug that is no page at all is removed. ``None`` is every page, the
    linker's proposed concepts included: the local backfill.
    """
    links = ctx.store.links(ctx.paths.links)
    prose = ctx.store.prose(ctx.paths.prose)
    assert links is not None and prose is not None
    merges = ctx.merges
    export, overlay, _ = link.build_overlay(ctx.export, links, merges)
    corpus = generate.build_corpus(export, overlay, links, merges)
    export = generate.apply_prose(export, prose, merges)
    context = generate.context_by_entity(export, prose, merges)
    details = generate.details_by_entity(export, merges, prose)

    exists = {e.slug for e in export.entities}
    folded = dict(merges.by_slug) if merges else {}
    targets: set[str] = set()
    remove: set[str] = set()
    for slug in exists if slugs is None else slugs:
        target = folded.get(slug, slug)
        if target in exists:
            targets.add(target)
        if target != slug or target not in exists:
            remove.add(slug)
    remove |= {old for old, new in folded.items() if new in targets}
    remove -= targets

    evidence = {
        slug: {
            e.source_id
            for e in corpus.own.get(slug, []) + corpus.referring.get(slug, [])
            if e.source_id is not None
        }
        for slug in targets
    }
    documents = pages.build_documents(
        export,
        overlay=overlay,
        context=context,
        details=details,
        merges=merges,
        evidence=evidence,
        only=targets,
    )
    counts = ctx.store.save_pages(
        [documents[s] for s in sorted(documents)], sorted(remove), ctx.paths.pages
    )
    return {
        "job": "materialise",
        "entities": sorted(targets),
        "removed": sorted(remove),
        "written": counts["written"],
        "next": [],
    }


# ── The worker's entry ──


def validate(job: dict[str, Any]) -> str:
    """The job's kind, or :class:`JobError` — before anything is built or called."""
    if not isinstance(job, dict):
        raise JobError("a job is a JSON object")
    kind = job.get("job")
    if kind == "link":
        if not job.get("source_id"):
            raise JobError("link needs source_id")
        try:
            uuid.UUID(str(job["source_id"]))
        except ValueError as exc:
            raise JobError("link's source_id is not a UUID") from exc
    elif kind == "consolidate":
        entities = job.get("entities")
        if not isinstance(entities, list) or not entities:
            raise JobError("consolidate needs a non-empty entities list")
    elif kind == "generate":
        entities = job.get("entities")
        if not job.get("entity") and not (isinstance(entities, list) and entities):
            raise JobError("generate needs entity or a non-empty entities list")
    elif kind == "materialise":
        entities = job.get("entities")
        if not isinstance(entities, list) or not entities:
            raise JobError("materialise needs a non-empty entities list")
    else:
        raise JobError(f"unknown job {kind!r}; expected one of {', '.join(JOBS)}")
    return str(kind)


def handle(job: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Run one job message. Returns its result and the jobs to enqueue next."""
    kind = validate(job)
    if kind == "link":
        result = link_source(ctx, job["source_id"])
    elif kind == "consolidate":
        result = consolidate_entities(ctx, [str(e) for e in job["entities"]])
    elif kind == "materialise":
        result = materialise_pages(ctx, [str(e) for e in job["entities"]])
    else:
        named = job.get("entities") or [job["entity"]]
        result = generate_entities(ctx, [str(e) for e in named])
    LOG.info("unit.done job=%s next=%s", kind, len(result.get("next", [])))
    return result
