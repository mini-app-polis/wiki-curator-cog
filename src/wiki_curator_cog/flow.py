"""The local commands, and what the wiki-jobs worker shares with them.

Every command here runs on a laptop against the production API: ``render``
writes the wcs-wiki markdown export from the page documents, and ``link``,
``consolidate``, ``generate``, ``materialise`` and ``job`` run the curator's
passes, with their state held by the API (ADR-006). The worker
(:mod:`wiki_curator_cog.worker`) uses the same export read, corpus check and
state client.

There is no deployed run here. The Railway ``export`` mode, which cloned
wcs-wiki, rendered and pushed, was retired with its service (MINI-95,
MINI-99): the worker stores each page as a document in the API, and the
markdown repo is only a local export.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import WcsWikiExportItem

from . import (
    consolidate,
    generate,
    identity,
    link,
    state_backend,
    state_store,
    units,
    usage,
)
from .render import (
    build_indexes,
    corpus_quality,
    find_broken_links,
    list_stale_derived_paths,
    render_bundle,
)

LOG = log.get_logger()

REPO = "wiki-curator-cog"
"""Machine name this cog reports under; also names its API key variable."""


#: This cog's name in api-kaianolevine-com's identity_registry.MACHINES. The
#: shared client derives WIKI_CURATOR_COG_API_KEY from it, and the API derives
#: the same variable from the same name, so the audit trail records which cog
#: read the corpus.
MACHINE_NAME = "wiki-curator-cog"


def _fetch_export() -> WcsWikiExportItem:
    """GET /v1/wcs/wiki/export — the full canonical corpus, in one call.

    Reads everything regardless of per-source visibility, which takes
    ``wcs.corpus.read`` and nothing more: reading is not administering.
    """
    return KaianoApiClient.from_env(MACHINE_NAME).export_wcs_wiki()


class EmptyExportError(RuntimeError):
    """The export holds no entities or no sources; nothing is written."""


def _require_corpus(export: WcsWikiExportItem) -> None:
    """Refuse an export with no entities or no sources, before anything is written.

    Every derived page is rewritten from the export and every page it does
    not produce is removed, so an empty export renders as "delete the whole
    wiki". An empty corpus is far likelier to mean the wrong environment or a
    broken API than a corpus that was emptied on purpose — the first local
    render against a dev API with no data removed 1829 pages from the working
    tree. If the corpus ever is emptied on purpose, deleting the wiki is a
    decision to make by hand, not a side effect of a render.
    """
    if not export.entities or not export.sources:
        raise EmptyExportError(
            f"export has {len(export.entities)} entities and "
            f"{len(export.sources)} sources; refusing to render over the wiki. "
            "Check which API this run is pointed at."
        )


def _write_bundle(wiki_repo_path: Path, bundle: dict[str, str]) -> list[Path]:
    written: list[Path] = []
    for rel_path, content in sorted(bundle.items()):
        out = wiki_repo_path / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        written.append(out)
    return written


def _existing_markdown(wiki_repo_path: Path) -> set[str]:
    return {
        str(p.relative_to(wiki_repo_path))
        for p in wiki_repo_path.rglob("*.md")
        if ".git" not in p.relative_to(wiki_repo_path).parts
    }


def _state(state: state_backend.FileState | None) -> state_backend.FileState:
    """The store a local command uses: files unless it was given another."""
    return state if state is not None else state_backend.FileState()


def _facet_parents(
    facets_path: Path | None, state: state_backend.FileState | None = None
) -> dict[str, tuple[str, bool]] | None:
    """The folds from the facets state, or None when there is none."""
    if facets_path is None:
        return None
    facets = _state(state).facets(facets_path, required=True)
    return facets.folds() if facets is not None else None


def render_local(
    wiki_repo_path: Path,
    links_path: Path | None = None,
    prose_path: Path | None = None,
    facets_path: Path | None = None,
    state: state_backend.FileState | None = None,
) -> dict:
    """Render into an existing local clone. No clone, commit, push or report.

    ``state`` is where links, prose and facets are read from: the files at
    the paths given, or (``ApiState``) the API, with those paths as its
    cache. A path left out means that layer is not used either way.

    The wcs-wiki repo is a local export (MINI-99): run this, read the result
    in Obsidian or ``git diff``, commit by hand if you want to. Entity pages
    are written from the page documents ``materialise`` stores. Nothing
    leaves the machine except the API reads.

    ``log.md`` is not touched. Stale derived pages are removed from the
    working tree, so ``git status`` shows everything that changed.
    """
    if not (wiki_repo_path / "CLAUDE.md").exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not look like a wcs-wiki clone "
            f"(no CLAUDE.md found): {wiki_repo_path}"
        )

    export = _fetch_export()
    _require_corpus(export)
    store = _state(state)
    export, merges = identity.resolve(export, _facet_parents(facets_path, store))

    overlay = None
    overlay_summary: dict | None = None
    if links_path is not None:
        links = store.links(links_path, required=True)
        assert links is not None
        export, overlay, ostats = link.build_overlay(export, links, merges)
        overlay_summary = {
            "file": str(links_path),
            "statements": ostats.statements,
            "pairs": ostats.pairs,
            "denied_pairs": ostats.denied_pairs,
            "proposed_pages": ostats.proposed_pages,
        }

    prose_summary: dict | None = None
    context = None
    prose = None
    if prose_path is not None:
        prose = store.prose(prose_path, required=True)
        assert prose is not None
        export = generate.apply_prose(export, prose, merges)
        context = generate.context_by_entity(export, prose, merges)
        prose_summary = {
            "file": str(prose_path),
            "definitions": sum(1 for e in export.entities if e.overview_md.strip()),
            "contexts": len(context),
        }

    details = generate.details_by_entity(export, merges, prose)
    bundle, stats = render_bundle(
        export,
        rendered_at=dt.date.today(),
        overlay=overlay,
        context=context,
        details=details,
    )
    bundle.pop("log.md", None)

    stale = list_stale_derived_paths(wiki_repo_path, set(bundle))
    for path in stale:
        path.unlink(missing_ok=True)
    written = _write_bundle(wiki_repo_path, bundle)

    broken = find_broken_links(
        bundle, existing_paths=_existing_markdown(wiki_repo_path)
    )
    summary = {
        "wiki_repo_path": str(wiki_repo_path),
        "entities": stats.entity_count,
        "sources": stats.source_count,
        "instructors": stats.instructor_count,
        "pages_rendered": len(written),
        "pages_removed": [str(p.relative_to(wiki_repo_path)) for p in stale],
        "broken_links": {
            "count": len(broken),
            "links": [{"page": page, "target": target} for page, target in broken],
        },
        "dropped": [{"reason": r, "reference": ref} for r, ref in stats.dropped],
        "quality": corpus_quality(export, build_indexes(export)),
        "links": overlay_summary,
        "prose": prose_summary,
        "merged": {
            old: new
            for old, new in sorted(merges.by_slug.items())
            if not any(f.slug == old for fs in merges.facets.values() for f in fs)
        },
        "facets": {
            "file": str(facets_path) if facets_path else None,
            "folded": sum(len(fs) for fs in merges.facets.values()),
            "into_pages": len(merges.facets),
        },
    }
    LOG.info(
        "render.local.complete pages=%s removed=%s broken_links=%s dropped=%s",
        len(written),
        len(stale),
        len(broken),
        len(stats.dropped),
    )
    return summary


def link_local(
    links_path: Path,
    *,
    sample: int | None = None,
    batch_size: int = link.DEFAULT_BATCH_SIZE,
    workers: int = 1,
    facets_path: Path | None = None,
    dry_run: bool = False,
    state: state_backend.FileState | None = None,
) -> dict:
    """Run the linking pass locally: references to the state store.

    With ``dry_run``, nothing is called and nothing is written: the summary
    says how many calls a real run would make and what they would cost.

    ADR-005 / MINI-84, local half. Reads the export (read-only), links every
    statement not already in ``links_path`` under the current linker version
    — or a reproducible sample of ``sample`` of them — and saves after every
    batch, so a run cut short keeps what it paid for. Writes a readable
    review next to the links file covering the statements this run was asked
    about.
    """
    export = _fetch_export()
    _require_corpus(export)
    # Link against the merged corpus, so a merged-away name resolves to its
    # preferred entity rather than being proposed as a new concept.
    store = _state(state)
    export, _ = identity.resolve(export, _facet_parents(facets_path, store))

    statements = link.collect_statements(export)
    chosen = (
        link.sample_statements(statements, sample) if sample is not None else statements
    )
    catalog = link.Catalog(export.entities)
    links = store.links(links_path)
    assert links is not None
    llm, model = link.llm_from_env(dry_run=dry_run)

    LOG.info(
        "link.start statements=%s chosen=%s catalog=%s model=%s file=%s",
        len(statements),
        len(chosen),
        len(catalog),
        model,
        links_path,
    )
    counts = link.run_linking(
        chosen,
        catalog,
        links,
        llm,
        model=model,
        batch_size=batch_size,
        on_batch=None if dry_run else (lambda: store.save(links, links_path)),
        workers=workers,
    )
    if dry_run:
        return _dry_summary("link", llm, counts)
    store.save(links, links_path, final=True)

    keys = [s.key for s in chosen]
    review_path = links_path.with_name(links_path.stem + "-review.md")
    review_path.write_text(link.review_markdown(links, keys))

    summary = {
        "links_file": str(links_path),
        "review_file": str(review_path),
        "linker_version": link.LINKER_VERSION,
        "model": model,
        "statements_in_corpus": len(statements),
        **counts,
        **link.summarize(links, keys),
        "usage": usage.usage_of(llm),
    }
    LOG.info(
        "link.complete linked=%s skipped=%s references=%s new=%s",
        counts["linked"],
        counts["skipped"],
        summary["references"],
        len(summary["new_concepts"]),
    )
    return summary


def generate_local(
    prose_path: Path,
    *,
    links_path: Path | None = None,
    wiki_repo_path: Path | None = None,
    entities: list[str] | None = None,
    sample: int | None = None,
    workers: int = generate.DEFAULT_WORKERS,
    condense: bool = False,
    facets_path: Path | None = None,
    dry_run: bool = False,
    state: state_backend.FileState | None = None,
) -> dict:
    """Run generation locally: overviews to a JSON file, nothing to the API.

    With ``dry_run``, nothing is called and nothing is written: the summary
    says how many calls a real run would make and what they would cost.

    With ``condense``, no new evidence is integrated: only pages whose prose
    has outgrown the fixed shape are rewritten to fit it.

    ADR-005 / MINI-86, local half. Reads the export (read-only) and, when
    given, the links file — for the concepts it proposed and the statements
    that refer to each entity. ``wiki_repo_path`` is where the current pages
    are read from, so an overview edited on the page becomes the base. Saves
    after every entity, and writes a readable review next to the prose file.
    """
    export = _fetch_export()
    _require_corpus(export)

    store = _state(state)
    export, merges = identity.resolve(export, _facet_parents(facets_path, store))
    overlay = None
    links = None
    if links_path is not None and (
        isinstance(store, state_backend.ApiState) or links_path.exists()
    ):
        links = store.links(links_path)
        assert links is not None
        export, overlay, _ = link.build_overlay(export, links, merges)

    corpus = generate.build_corpus(export, overlay, links, merges)
    prose = store.prose(prose_path)
    assert prose is not None
    if condense:
        llm, model = link.llm_from_env(dry_run=dry_run)
        counts = generate.run_condense(
            corpus,
            prose,
            llm,
            model=model,
            workers=workers,
            on_entity=None if dry_run else (lambda: store.save(prose, prose_path)),
        )
        if dry_run:
            return _dry_summary("condense", llm, counts)
        store.save(prose, prose_path, final=True)
        review_path = prose_path.with_name(prose_path.stem + "-review.md")
        review_path.write_text(generate.review_markdown(prose, counts["slugs"]))
        LOG.info(
            "condense.complete over_shape=%s condensed=%s",
            counts["over_shape"],
            counts["condensed"],
        )
        return {
            "prose_file": str(prose_path),
            "review_file": str(review_path),
            "model": model,
            **counts,
            "usage": usage.usage_of(llm),
        }

    chosen = generate.choose_entities(
        corpus, prose, slugs=entities or (), sample=sample
    )
    llm, model = link.llm_from_env(dry_run=dry_run)

    LOG.info(
        "generate.start entities=%s model=%s file=%s links=%s",
        len(chosen),
        model,
        prose_path,
        links_path if links is not None else None,
    )
    counts = generate.run_generation(
        chosen,
        corpus,
        prose,
        llm,
        model=model,
        wiki_repo_path=wiki_repo_path,
        on_entity=None if dry_run else (lambda: store.save(prose, prose_path)),
        workers=workers,
    )
    if dry_run:
        return _dry_summary("generate", llm, counts)
    store.save(prose, prose_path, final=True)

    slugs = [e.slug for e in chosen]
    review_path = prose_path.with_name(prose_path.stem + "-review.md")
    review_path.write_text(generate.review_markdown(prose, slugs))
    summary = {
        "prose_file": str(prose_path),
        "review_file": str(review_path),
        "generator_version": generate.GENERATOR_VERSION,
        "model": model,
        "used_links": links is not None,
        **counts,
        "slugs": slugs,
        "usage": usage.usage_of(llm),
    }
    LOG.info("generate.complete %s", counts)
    return summary


def consolidate_local(
    facets_path: Path,
    *,
    prose_path: Path | None = None,
    links_path: Path | None = None,
    sample: int | None = None,
    batch_size: int = consolidate.DEFAULT_BATCH_SIZE,
    workers: int = consolidate.DEFAULT_WORKERS,
    dry_run: bool = False,
    state: state_backend.FileState | None = None,
) -> dict:
    """Decide which entities are facets of broader ones: to the state store.

    With ``dry_run``, nothing is called and nothing is written: the summary
    says how many calls a real run would make and what they would cost.

    MINI-93, local half. Runs on the corpus after the notes' alias merges, so
    a merged-away name is not decided twice. With ``prose_path``, the model
    sees each entity's generated definition. Saves after every batch, and
    writes a review grouped by broader page next to the facets file.
    """
    export = _fetch_export()
    _require_corpus(export)
    export, merges = identity.resolve(export)

    store = _state(state)
    api = isinstance(store, state_backend.ApiState)
    links = None
    if links_path is not None and (api or links_path.exists()):
        links = store.links(links_path)
    supports = consolidate.support(export, links, merges)

    definitions = None
    if prose_path is not None and (api or prose_path.exists()):
        prose = store.prose(prose_path)
        assert prose is not None
        definitions = {e.slug: prose.definition(e.slug) for e in export.entities}

    facets = store.facets(facets_path)
    assert facets is not None
    llm, model = link.llm_from_env(dry_run=dry_run)
    LOG.info("consolidate.start model=%s file=%s", model, facets_path)
    counts = consolidate.run_consolidation(
        export,
        facets,
        llm,
        model=model,
        definitions=definitions,
        supports=supports,
        batch_size=batch_size,
        workers=workers,
        sample=sample,
        on_batch=None if dry_run else (lambda: store.save(facets, facets_path)),
    )
    if dry_run:
        return _dry_summary("consolidate", llm, counts)
    store.save(facets, facets_path, final=True)

    names = {e.slug: e.canonical_name for e in export.entities}
    review_path = facets_path.with_name(facets_path.stem + "-review.md")
    review_path.write_text(consolidate.review_markdown(facets, names, supports))
    thin_path = facets_path.with_name("thin-review.md")
    thin_path.write_text(
        consolidate.thin_review_markdown(export, facets, supports, definitions)
    )
    LOG.info("consolidate.complete %s", counts)
    return {
        "facets_file": str(facets_path),
        "review_file": str(review_path),
        "thin_review_file": str(thin_path),
        "consolidator_versions": dict(consolidate.CONSOLIDATOR_VERSIONS),
        "model": model,
        "used_links": links is not None,
        "thin_entities": sum(
            1 for c in consolidate.candidates(export, None, supports) if c.support.thin
        ),
        **counts,
        "usage": usage.usage_of(llm),
    }


#: Reading the whole prose document is several megabytes; the client's
#: default timeout is meant for single-row calls.
_STATE_TIMEOUT_S = 180.0


def _state_client() -> KaianoApiClient:
    return KaianoApiClient(machine_name=MACHINE_NAME, timeout=_STATE_TIMEOUT_S)


def run_job(
    job: dict,
    state_dir: Path,
    *,
    state: state_backend.FileState | None = None,
    wiki_repo_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Run one worker job locally, as the wiki-jobs worker would (MINI-94).

    The same :func:`wiki_curator_cog.units.handle` the worker calls, against
    the production export and the given state store. With ``dry_run`` the
    model is not called and nothing is written; the summary shows the calls
    the job would make.
    """
    units.validate(job)
    export = _fetch_export()
    _require_corpus(export)
    store = _state(state)
    llm, model = link.llm_from_env(dry_run=dry_run)
    if dry_run:
        store = _NoWrites(store)
    ctx = units.build_context(
        export,
        store,
        units.StatePaths.under(state_dir),
        llm=llm,
        model=model,
        wiki_repo_path=wiki_repo_path,
    )
    result = units.handle(job, ctx)
    return {**result, "dry_run": dry_run, "usage": usage.usage_of(llm)}


def materialise_local(
    state_dir: Path,
    *,
    entities: list[str] | None = None,
    state: state_backend.FileState | None = None,
    dry_run: bool = False,
) -> dict:
    """Materialise pages: the named ones, or every page (MINI-96).

    No model call. With the API as the store this writes the page documents
    to the API the environment points at — production under
    ``doppler run --config prd``. Every page at once is the backfill, and the
    way to rebuild the documents after the page logic changes. With
    ``dry_run`` the documents are built and counted but not stored.
    """
    export = _fetch_export()
    _require_corpus(export)
    store = _state(state)
    if dry_run:
        store = _NoWrites(store)
    ctx = units.build_context(
        export,
        store,
        units.StatePaths.under(state_dir),
        llm=None,
        model="",
    )
    result = units.materialise_pages(ctx, entities or None)
    summary = {
        "dry_run": dry_run,
        "pages": len(result["entities"]),
        # names folded into a page, or no longer pages: asked to be removed
        "gone": len(result["removed"]),
        "written": result["written"],
        # of those, the ones that had a stored page
        "deleted": result["deleted"],
    }
    LOG.info("materialise.local %s", summary)
    return summary


class _NoWrites(state_backend.FileState):
    """A store that reads through to another and writes nothing: dry runs."""

    def __init__(self, inner: state_backend.FileState) -> None:
        self._inner = inner
        self.name = f"{inner.name} (dry run)"

    def links(self, path: Path, *, required: bool = False):  # type: ignore[no-untyped-def]
        """Read the links document through the wrapped store."""
        return self._inner.links(path, required=required)

    def facets(self, path: Path, *, required: bool = False):  # type: ignore[no-untyped-def]
        """Read the facets document through the wrapped store."""
        return self._inner.facets(path, required=required)

    def prose(self, path: Path, *, required: bool = False):  # type: ignore[no-untyped-def]
        """Read the prose document through the wrapped store."""
        return self._inner.prose(path, required=required)

    def save(self, obj, path: Path, *, final: bool = False) -> None:  # type: ignore[no-untyped-def]
        """Discard the write: a dry run changes no state."""
        return None

    def save_pages(self, pages, remove, folder: Path) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """Discard the pages: a dry run stores nothing."""
        return {
            "written": 0,
            "removed": 0,
            "created": [],
            "changed": [],
            "removed_slugs": [],
        }


def api_state() -> state_backend.ApiState:
    """The API as the state store, through this cog's own key (MINI-94)."""
    return state_backend.ApiState(_state_client())


def state_import_local(
    state_dir: Path, *, chunk: int = state_store.DEFAULT_CHUNK
) -> dict:
    """Send links.json, facets.json and prose.json to the API (MINI-89).

    This writes to the API the environment points at — production under
    ``doppler run --config prd``. Upserts only: re-running it is safe, and
    nothing in the files changes. Run ``state-verify`` afterwards.
    """
    summary = state_store.import_state(_state_client(), state_dir, chunk=chunk)
    LOG.info("state.import %s", summary)
    return {"state_dir": str(state_dir), **summary}


def state_verify_local(state_dir: Path) -> dict:
    """Whether the API holds exactly what the three files hold. Read-only."""
    summary = state_store.verify_state(_state_client(), state_dir)
    LOG.info("state.verify ok=%s", summary["ok"])
    return {"state_dir": str(state_dir), **summary}


def _dry_summary(command: str, llm: Any, counts: dict) -> dict:
    """What a real run would do: the work it found, the calls, the cost.

    Counts that depend on the model's answers (decided, linked, generated)
    are left out — a dry run gets no answers.
    """
    found = {
        k: v
        for k, v in counts.items()
        if k
        in {
            "statements",
            "skipped",
            "pending",
            "pending_by_group",
            "candidates",
            "entities",
            "over_shape",
        }
    }
    summary = {
        "dry_run": True,
        "command": command,
        **found,
        "usage": usage.usage_of(llm),
    }
    LOG.info("%s.dry_run %s", command, summary)
    return summary
