"""Prefect flows for wiki-curator-cog.

Two production flows:

  backfill_flow         — one-time, processes the entire upstream corpus
                          in chronological order.
  incremental_flow      — steady-state, processes notes created since
                          the last successful run.

Both delegate per-source work to wiki_curator_cog.curator.ingest_one_source.
"""

from __future__ import annotations

import datetime as dt

import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency

from .aliases import AliasMap
from .api_client import WikiCuratorApiClient
from .config import Config, load_config
from .curator import IngestMode, ingest_one_source
from .git_ops import WikiRepo
from .inventory import build_inventory
from .state import load_state, save_state

load_dotenv()

LOG = log.get_logger()


def _get_logger():
    """Dual logger pattern per PIPE-006."""
    try:
        return get_run_logger()
    except Exception:
        return LOG


@flow(name="wiki-curator-cog-backfill")
def backfill_flow() -> dict:
    """One-time backfill of the entire upstream corpus.

    Reads every WCS note from api-kaianolevine-com in chronological order
    and ingests it into the wiki repo. Idempotent: re-running is safe;
    notes already present at the current curator_version are skipped.
    """
    logger = _get_logger()
    config = load_config()

    with concurrency("wiki-curator-cog", occupy=1):
        logger.info("backfill.start curator_version=%s", config.curator_version)

        api = WikiCuratorApiClient()
        wiki_repo = WikiRepo(config)
        inventory = build_inventory(config.wiki_repo_path)
        aliases = AliasMap.load(config.wiki_repo_path)

        total = 0
        ingested = 0
        skipped = 0

        for note in api.iter_all_notes(page_size=config.backfill_page_size):
            total += 1
            try:
                result = ingest_one_source(
                    note=note,
                    mode=IngestMode.BACKFILL,
                    inventory=inventory,
                    aliases=aliases,
                    wiki_repo_path=config.wiki_repo_path,
                    api=api,
                    curator_version=config.curator_version,
                )
            except NotImplementedError:
                # Skeleton stub — re-raise so the flow fails loudly while
                # the curator is still under construction.
                raise
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                logger.error("backfill.note_failed note_id=%s err=%s", note.id, exc)
                continue

            if result.skipped:
                skipped += 1
                continue
            ingested += 1
            if result.touched_paths:
                wiki_repo.stage(result.touched_paths)
                wiki_repo.commit(f"ingest: {result.source_path.stem}")  # type: ignore[union-attr]

        # Save aliases once at end of backfill (may have grown).
        aliases.save()
        if wiki_repo.has_changes():
            wiki_repo.stage_all()
            wiki_repo.commit("backfill: alias map and residual updates")

        wiki_repo.push()

        save_state(
            config,
            last_run_at=dt.datetime.now(dt.UTC),
            curator_version_at_last_run=config.curator_version,
        )

        summary = {
            "total": total,
            "ingested": ingested,
            "skipped": skipped,
            "curator_version": config.curator_version,
        }
        logger.info("backfill.complete %s", summary)
        return summary


@flow(name="wiki-curator-cog-incremental")
def incremental_flow() -> dict:
    """Process notes created since the last successful run.

    Triggered downstream of transcription-cog completion (Phase 2). Uses
    the API's `since` filter; the cutoff is the last-run timestamp from
    local state.

    Phase 1.5 work: the `since` filter must land on
    GET /v1/wcs/notes/all before this flow is useful in production.
    """
    logger = _get_logger()
    config = load_config()

    with concurrency("wiki-curator-cog", occupy=1):
        state = load_state(config)
        since = state.last_run_at
        logger.info(
            "incremental.start since=%s curator_version=%s",
            since,
            config.curator_version,
        )

        api = WikiCuratorApiClient()
        wiki_repo = WikiRepo(config)
        inventory = build_inventory(config.wiki_repo_path)
        aliases = AliasMap.load(config.wiki_repo_path)

        total = 0
        ingested = 0
        skipped = 0

        for note in api.iter_all_notes(
            page_size=config.backfill_page_size, since=since
        ):
            total += 1
            try:
                result = ingest_one_source(
                    note=note,
                    mode=IngestMode.AUTOMATED,
                    inventory=inventory,
                    aliases=aliases,
                    wiki_repo_path=config.wiki_repo_path,
                    api=api,
                    curator_version=config.curator_version,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                logger.error("incremental.note_failed note_id=%s err=%s", note.id, exc)
                continue

            if result.skipped:
                skipped += 1
                continue
            ingested += 1
            if result.touched_paths:
                wiki_repo.stage(result.touched_paths)
                wiki_repo.commit(f"ingest: {result.source_path.stem}")  # type: ignore[union-attr]

        aliases.save()
        if wiki_repo.has_changes():
            wiki_repo.stage_all()
            wiki_repo.commit("incremental: alias map and residual updates")

        wiki_repo.push()

        save_state(
            config,
            last_run_at=dt.datetime.now(dt.UTC),
            curator_version_at_last_run=config.curator_version,
        )

        summary = {
            "total": total,
            "ingested": ingested,
            "skipped": skipped,
            "curator_version": config.curator_version,
        }
        logger.info("incremental.complete %s", summary)
        return summary
