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
                                                      entity it taught or
                                                      referred to
``consolidate``           named undecided entities    ``generate`` for each page
                                                      that gained a fold
``generate``              one entity                  — (``materialise``, MINI-96)
========================  ==========================  ===========================

Every unit is idempotent under its pass's watermark: a statement already
linked, an entity already decided, an entity with nothing new to integrate
costs no model call and writes nothing. So a retried or duplicated message is
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

from . import consolidate, generate, identity, link, state_backend
from .render import WcsWikiExport

LOG = log.get_logger()

JOBS = ("link", "consolidate", "generate")


class JobError(ValueError):
    """A job message this worker cannot run."""


@dataclass(frozen=True)
class StatePaths:
    """Where each state document is cached (or kept, with ``FileState``)."""

    links: Path
    facets: Path
    prose: Path

    @classmethod
    def under(cls, folder: Path) -> StatePaths:
        return cls(
            links=folder / "links.json",
            facets=folder / "facets.json",
            prose=folder / "prose.json",
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
    folds = facets.folds() if facets is not None else None
    merged, merges = identity.resolve(export, folds)
    return RunContext(
        export=merged,
        merges=merges,
        store=store,
        paths=paths,
        llm=llm,
        model=model,
        wiki_repo_path=wiki_repo_path,
        raw_export=export,
    )


def _job(kind: str, **fields: Any) -> dict[str, Any]:
    return {"job": kind, **fields}


# ── link ──


def link_source(ctx: RunContext, source_id: uuid.UUID | str) -> dict[str, Any]:
    """Link one source's statements; follow with consolidate and generate."""
    sid = uuid.UUID(str(source_id))
    statements = [s for s in link.collect_statements(ctx.export) if s.source_id == sid]
    links = ctx.store.links(ctx.paths.links)
    assert links is not None
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
    for s in statements:
        touched.add(s.subject_slug)
        entry = links.statements.get(s.key) or {}
        touched.update(r["target_slug"] for r in entry.get("references", []))

    facets = ctx.store.facets(ctx.paths.facets)
    assert facets is not None
    version_of = {k: g.version for g in consolidate.GROUPS for k in g.kinds}
    by_slug = {e.slug: e for e in ctx.export.entities}
    undecided = sorted(
        slug
        for slug in {s.subject_slug for s in statements}
        if slug in by_slug
        and by_slug[slug].kind in version_of
        and not facets.is_decided(slug, version_of[by_slug[slug].kind])
    )
    follow = ([_job("consolidate", entities=undecided)] if undecided else []) + [
        _job("generate", entity=slug) for slug in sorted(touched)
    ]
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
    facets = ctx.store.facets(ctx.paths.facets)
    assert facets is not None
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
        "next": [_job("generate", entity=slug) for slug in gained],
    }


# ── generate ──


def generate_entity(ctx: RunContext, slug: str) -> dict[str, Any]:
    """Integrate one entity's pending statements. No call if there are none.

    A slug folded away since the job was enqueued is generated as the page it
    folded into; one that no longer exists at all is skipped.
    """
    links = ctx.store.links(ctx.paths.links)
    assert links is not None
    export, overlay, _ = link.build_overlay(ctx.export, links, ctx.merges)
    corpus = generate.build_corpus(export, overlay, links, ctx.merges)
    target = ctx.merges.by_slug.get(slug, slug) if ctx.merges else slug
    entity = corpus.entities.get(target)
    if entity is None:
        return {
            "job": "generate",
            "entity": slug,
            "skipped": "no such entity",
            "next": [],
        }
    prose = ctx.store.prose(ctx.paths.prose)
    assert prose is not None
    counts = generate.run_generation(
        [entity],
        corpus,
        prose,
        ctx.llm,
        model=ctx.model,
        wiki_repo_path=ctx.wiki_repo_path,
    )
    ctx.store.save(prose, ctx.paths.prose, final=True)
    return {
        "job": "generate",
        "entity": target,
        **{k: counts[k] for k in ("generated", "failed")},
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
        if not job.get("entity"):
            raise JobError("generate needs entity")
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
    else:
        result = generate_entity(ctx, str(job["entity"]))
    LOG.info("unit.done job=%s next=%s", kind, len(result.get("next", [])))
    return result
