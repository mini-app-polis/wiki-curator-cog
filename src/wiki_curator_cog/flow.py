"""Prefect flow for wiki-curator-cog.

Single production flow: export_flow fetches the canonical entity graph,
renders the full markdown bundle, and commits once per run.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency

from .api_client import WikiCuratorApiClient
from .boot import ensure_wiki_clone, mask_url
from .config import assert_wiki_clone_ready, load_config
from .git_ops import WikiRepo
from .render import list_stale_derived_paths, render_bundle

load_dotenv()

LOG = log.get_logger()


def _get_logger():
    """Dual logger pattern per PIPE-006."""
    try:
        return get_run_logger()
    except Exception:
        return LOG


def _write_bundle(wiki_repo_path: Path, bundle: dict[str, str]) -> list[Path]:
    written: list[Path] = []
    for rel_path, content in sorted(bundle.items()):
        out = wiki_repo_path / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        written.append(out)
    return written


@flow(name="wiki-curator-cog-export")
def export_flow() -> dict:
    """Fetch canonical export and re-render the full wiki bundle."""
    logger = _get_logger()
    config = load_config()

    with concurrency("wiki-curator-cog", occupy=1):
        logger.info(
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
            # Pages actually in the bundle, which is not the same as
            # len(export.sources) once two sources collide on a slug.
            "source_pages": sum(1 for p in bundle if p.startswith("sources/")),
            "dropped": list(stats.dropped),
        }
        logger.info("export.complete %s", summary)
        return summary
