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
    :func:`mini_app_polis.pipeline_status.run_report`, which records the
    exception as an issue, sends the report, and re-raises. One run, one
    report — the hook and the report used to be two messages about one bad
    run, told apart by ``source``; now there is only the report.

``get_run_id()``
    A uuid4 minted by ``main`` and threaded in. The library's fallback
    resolves Prefect's ids and answers ``"local-run"`` without them, which
    would make every report unattributable and unjoinable to the findings
    the same run filed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mini_app_polis import logger as log
from mini_app_polis.pipeline_status import RunReport, run_report

from ._deadline import deadline
from .api_client import WikiCuratorApiClient
from .boot import ensure_wiki_clone, mask_url
from .config import Config, assert_wiki_clone_ready, load_config
from .git_ops import WikiRepo
from .render import list_stale_derived_paths, render_bundle

load_dotenv()

LOG = log.get_logger()

REPO = "wiki-curator-cog"
"""Machine name this cog reports under; also names its API key variable."""

FLOW_NAME = "wiki-curator-cog-export"
"""What the run reports call themselves. Unchanged from the Prefect name."""


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

    ensure_wiki_clone(config)
    assert_wiki_clone_ready(config)

    api = WikiCuratorApiClient()
    wiki_repo = WikiRepo(config)

    export = api.fetch_export()
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

    commit_msg = (
        f"render: {rendered_at.isoformat()} "
        f"({stats.entity_count} entities, {stats.source_count} sources)"
    )
    if wiki_repo.has_changes():
        wiki_repo.commit(commit_msg)

    wiki_repo.push()

    summary = {
        "entities": stats.entity_count,
        "sources": stats.source_count,
        "instructors": stats.instructor_count,
        "paths_written": len(written),
        "paths_removed": len(stale),
        # The paths themselves, not only how many. The run report names
        # what moved; a count is not something anyone can go and look at.
        "written_paths": [str(p) for p in written],
        "removed_paths": [str(p) for p in stale],
        # Pages actually in the bundle, which is not the same as
        # len(export.sources) once two sources collide on a slug.
        "source_pages": sum(1 for p in bundle if p.startswith("sources/")),
        "dropped": list(stats.dropped),
    }
    LOG.info("export.complete %s", summary)
    return summary


def _record_pages(record: Any, summary: dict, paths_key: str, count_key: str) -> None:
    """Record one page outcome per path, or an unnamed one per count."""
    paths = summary.get(paths_key)
    if paths:
        for path in paths:
            record("wiki page", str(path))
        return
    for _ in range(int(summary.get(count_key, 0) or 0)):
        record("wiki page")


def record_summary(report: RunReport, summary: dict) -> None:
    """Translate one export summary onto the run report.

    Severity is derived, never asserted: any dangling reference, colliding
    slug or unresolved instructor makes the run WARN, which is what this
    module means by "results worth a human look".
    """
    report.ok(int(summary.get("paths_written", 0)))
    for reason, ref in summary.get("dropped", []):
        report.issue(str(reason), str(ref))
    report.count("removed", summary.get("paths_removed", 0))
    report.count("entities", summary.get("entities", 0))
    report.count("sources", summary.get("sources", 0))
    source_pages = summary.get("source_pages")
    if source_pages is not None and source_pages != summary.get("sources"):
        report.count("source_pages", source_pages)

    # Which pages moved, not only how many. Falls back to unnamed outcomes
    # when the export reports counts without paths, so an older summary
    # shape still says that the wiki changed.
    _record_pages(report.created, summary, "written_paths", "paths_written")
    _record_pages(report.removed, summary, "removed_paths", "paths_removed")


def export_run(*, run_id: str) -> dict:
    """Run one export and report it, however it ends.

    The report is opened before the export rather than after, so the
    duration on it is the export's and not the microsecond it takes to
    fill in. No explicit notability: an export that rendered the same
    bundle as last time recorded no outcome and stays quiet; one that
    wrote or removed a path has one, and that is what makes it worth
    sending. A WARN is sent either way.
    """
    config = load_config()
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        with deadline(config.run_timeout_seconds):
            summary = _export(config)
        record_summary(report, summary)
    return summary
