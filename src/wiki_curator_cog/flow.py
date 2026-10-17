"""The export run: fetch the canonical entity graph, render, commit, push.

This was a Prefect flow. What each Prefect facility did, and what does it
now:

``@flow`` / ``serve()`` / the ``wiki_curator_router`` dispatcher
    Nothing. One wake is one run of one mode, so there is nothing to
    dispatch to and no resident process to dispatch from. ``main`` calls
    :func:`export_run` once and exits.

``concurrency("wiki-curator-cog", occupy=1)``
    Removed, not replaced, and deliberately. The slot existed because two
    runs would race on one working tree; a one-shot process clones into
    its own container's ``/tmp`` and shares nothing, so the race it
    prevented cannot happen. What two overlapping runs *could* still race
    on is the push, and git already arbitrates that: the loser is rejected
    non-fast-forward and fails visibly. That is weaker than a lock and
    strong enough for a manually-triggered rebuild; it would not be enough
    for anything automatic and frequent.

``@task(retries=...)``
    There were none in this flow. The one external call with a retry of
    its own is the push (``WikiRepo.push``, three attempts with backoff).
    The export GET retries inside ``KaianoApiClient``. Nothing else here
    is retried, and with no queue behind the run there is no redelivery
    to fall back on — a failed run is re-triggered by hand.

``on_failure`` / ``on_crashed`` (``make_failure_hook``)
    The explicit failure path in :func:`export_run`, which posts one ERROR
    carrying whatever the run had managed to do, and re-raises.

    Not ``pipeline_status.run_report``, though it is the obvious fit and was
    used first. Its severity is derived from the report, and a report has no
    verb for ERROR — the library says so, because ERROR used to mean "a flow
    died" and that was the Prefect hook's to say. Through ``run_report`` a
    run that died on a rejected push reported WARN, the same severity as one
    that merely dropped a dangling reference. So the report is built here and
    sent on exactly one of the two paths: ``send()`` on success, an explicit
    ERROR on failure. One run, one message, at the severity it deserves.

``get_run_id()``
    A uuid4 minted by ``main`` and threaded in. The library's fallback
    resolves Prefect's ids and answers ``"local-run"`` without them, which
    would make every report unattributable and unjoinable to the findings
    the same run filed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from mini_app_polis import logger as log
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import WcsWikiExportItem
from mini_app_polis.pipeline_status import RunReport, post_run_finding

from . import generate, identity, link
from ._deadline import deadline
from .boot import assert_push_access, ensure_wiki_clone, mask_url
from .config import Config, assert_wiki_clone_ready, load_config
from .git_ops import WikiRepo
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

FLOW_NAME = "wiki-curator-cog-export"
"""What the run reports call themselves. Unchanged from the Prefect name."""


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


def _export(config: Config) -> dict:
    """Fetch the canonical export and re-render the full wiki bundle."""
    LOG.info(
        "export.start branch=%s repo=%s",
        config.wiki_branch,
        mask_url(config.wiki_repo_url),
    )

    # Before the clone, not after the render: a credential that reads but
    # cannot write is otherwise discovered once the whole bundle is on disk
    # and committed. See boot.assert_push_access.
    assert_push_access(config)

    ensure_wiki_clone(config)
    assert_wiki_clone_ready(config)

    wiki_repo = WikiRepo(config)

    export = _fetch_export()
    _require_corpus(export)
    log_path = config.wiki_repo_path / "log.md"
    existing_log = log_path.read_text() if log_path.exists() else ""

    rendered_at = dt.date.today()
    bundle, stats = render_bundle(
        export,
        rendered_at=rendered_at,
        existing_log=existing_log,
    )

    expected_paths = set(bundle.keys())
    stale = list_stale_derived_paths(config.wiki_repo_path, expected_paths)
    for path in stale:
        path.unlink(missing_ok=True)

    written = _write_bundle(config.wiki_repo_path, bundle)

    if stale:
        wiki_repo.stage_removal(stale)
    if written:
        wiki_repo.stage(written)

    # After staging and before committing: this is the only moment the
    # index holds the whole changeset and HEAD still holds the old one.
    changes = wiki_repo.staged_changes()

    commit_msg = (
        f"render: {rendered_at.isoformat()} "
        f"({stats.entity_count} entities, {stats.source_count} sources)"
    )
    committed = wiki_repo.has_changes()
    if committed:
        wiki_repo.commit(commit_msg)

    wiki_repo.push()

    summary = {
        "entities": stats.entity_count,
        "sources": stats.source_count,
        "instructors": stats.instructor_count,
        # How much was rendered, and separately what that actually changed.
        # The first is very nearly a constant — every page is rewritten
        # every run — so only the second says whether the wiki moved.
        "pages_rendered": len(written),
        "added": changes["added"],
        "modified": changes["modified"],
        "removed": changes["removed"],
        "committed": committed,
        # Pages actually in the bundle, which is not the same as
        # len(export.sources) once two sources collide on a slug.
        "source_pages": sum(1 for p in bundle if p.startswith("sources/")),
        "dropped": list(stats.dropped),
    }
    LOG.info("export.complete %s", summary)
    return summary


def _existing_markdown(wiki_repo_path: Path) -> set[str]:
    return {
        str(p.relative_to(wiki_repo_path))
        for p in wiki_repo_path.rglob("*.md")
        if ".git" not in p.relative_to(wiki_repo_path).parts
    }


def render_local(
    wiki_repo_path: Path,
    links_path: Path | None = None,
    prose_path: Path | None = None,
) -> dict:
    """Render into an existing local clone. No clone, commit, push or report.

    The proof-of-concept loop (ADR-005, MINI-83): run it, read the result in
    Obsidian or ``git diff``, change something, run it again. Nothing leaves
    the machine except the export GET, so it needs the API key and nothing
    else — no GH_TOKEN, and no Discord message per iteration.

    ``log.md`` is not touched: a local iteration is not an event in the
    wiki's history. Stale derived pages are removed from the working tree,
    as an export would, so ``git status`` shows exactly what an export would
    commit.
    """
    if not (wiki_repo_path / "CLAUDE.md").exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not look like a wcs-wiki clone "
            f"(no CLAUDE.md found): {wiki_repo_path}"
        )

    export = _fetch_export()
    _require_corpus(export)
    export, merges = identity.resolve(export)

    overlay = None
    overlay_summary: dict | None = None
    if links_path is not None:
        if not links_path.exists():
            raise RuntimeError(f"links file not found: {links_path}")
        export, overlay, ostats = link.build_overlay(
            export, link.LinksFile.load(links_path), merges
        )
        overlay_summary = {
            "file": str(links_path),
            "statements": ostats.statements,
            "pairs": ostats.pairs,
            "denied_pairs": ostats.denied_pairs,
            "proposed_pages": ostats.proposed_pages,
        }

    prose_summary: dict | None = None
    context = None
    if prose_path is not None:
        if not prose_path.exists():
            raise RuntimeError(f"prose file not found: {prose_path}")
        prose = generate.ProseFile.load(prose_path)
        export = generate.apply_prose(export, prose)
        context = generate.context_by_entity(export, prose)
        prose_summary = {
            "file": str(prose_path),
            "definitions": sum(1 for e in export.entities if e.overview_md.strip()),
            "contexts": len(context),
        }

    bundle, stats = render_bundle(
        export, rendered_at=dt.date.today(), overlay=overlay, context=context
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
        "merged": dict(sorted(merges.by_slug.items())),
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
) -> dict:
    """Run the linking pass locally: references to a JSON file, nothing to the API.

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
    export, _ = identity.resolve(export)

    statements = link.collect_statements(export)
    chosen = (
        link.sample_statements(statements, sample) if sample is not None else statements
    )
    catalog = link.Catalog(export.entities)
    links = link.LinksFile.load(links_path)
    llm, model = link.llm_from_env()

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
        on_batch=lambda: links.save(links_path),
        workers=workers,
    )
    links.save(links_path)

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
) -> dict:
    """Run generation locally: overviews to a JSON file, nothing to the API.

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

    export, merges = identity.resolve(export)
    overlay = None
    links = None
    if links_path is not None and links_path.exists():
        links = link.LinksFile.load(links_path)
        export, overlay, _ = link.build_overlay(export, links, merges)

    corpus = generate.build_corpus(export, overlay, links, merges)
    prose = generate.ProseFile.load(prose_path)
    if condense:
        llm, model = link.llm_from_env()
        counts = generate.run_condense(
            corpus,
            prose,
            llm,
            model=model,
            workers=workers,
            on_entity=lambda: prose.save(prose_path),
        )
        prose.save(prose_path)
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
        }

    chosen = generate.choose_entities(
        corpus, prose, slugs=entities or (), sample=sample
    )
    llm, model = link.llm_from_env()

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
        on_entity=lambda: prose.save(prose_path),
        workers=workers,
    )
    prose.save(prose_path)

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
    }
    LOG.info("generate.complete %s", counts)
    return summary


def record_summary(report: RunReport, summary: dict) -> None:
    """Translate one export summary onto the run report.

    Outcomes are what git recorded, not what the renderer wrote. A page
    that was rewritten with identical bytes is not an outcome, and the
    run that rewrote 2505 of them and changed none has nothing to
    announce — which is what makes a SUCCESS worth staying quiet about.
    The work done is still visible, as the ``rendered`` counter.

    Severity is derived, never asserted: any dangling reference,
    colliding slug or unresolved instructor makes the run WARN, which is
    what this module means by "results worth a human look".
    """
    added = list(summary.get("added", []))
    modified = list(summary.get("modified", []))
    removed = list(summary.get("removed", []))

    report.ok(len(added) + len(modified) + len(removed))
    for reason, ref in summary.get("dropped", []):
        report.issue(str(reason), str(ref))

    report.count("rendered", summary.get("pages_rendered", 0))
    report.count("entities", summary.get("entities", 0))
    report.count("sources", summary.get("sources", 0))
    source_pages = summary.get("source_pages")
    if source_pages is not None and source_pages != summary.get("sources"):
        report.count("source_pages", source_pages)

    # Which pages moved, and how — a rewrite is an update, not a creation.
    for path in added:
        report.created("wiki page", str(path))
    for path in modified:
        report.updated("wiki page", str(path))
    for path in removed:
        report.removed("wiki page", str(path))


def export_run(*, run_id: str) -> dict:
    """Run one export and report it, however it ends.

    The report is opened before the export rather than after, so the
    duration on it is the export's and not the microsecond it takes to fill
    in. No explicit notability on the success path: an export whose commit
    was empty recorded no outcome and stays quiet; one that added, changed
    or removed a page has one, and that is what makes it worth sending. A
    WARN is sent either way.

    Exactly one message leaves this function. ``report.send()`` is never
    reached on the failure path, and the ERROR below is never posted on the
    success path.
    """
    config = load_config()
    report = RunReport(flow_name=FLOW_NAME, repo=REPO, run_id=run_id)

    try:
        with deadline(config.run_timeout_seconds):
            summary = _export(config)
    except BaseException as exc:  # noqa: BLE001 — reported, then re-raised
        # BaseException, not Exception: a run killed by SIGTERM mid-render
        # should still say what it had managed to do. Re-raised either way.
        report.issue("export_failed", type(exc).__name__, detail=str(exc))
        post_run_finding(
            FLOW_NAME,
            "ERROR",
            text=report.text(),
            repo=REPO,
            run_id=run_id,
            notable=True,
            **report.counters,
        )
        raise

    record_summary(report, summary)
    report.send()
    return summary
