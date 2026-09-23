"""Application entrypoint for wiki-curator-cog.

One invocation is one export. The process renders the wiki once and
exits; there is no resident loop and nothing to dispatch.

    python -m wiki_curator_cog.main          # what Railway's start command runs
    python -m wiki_curator_cog.main export   # a run asked for on purpose

**Those two are not quite the same thing, and the difference is the point.**
Starting the container is how a run is triggered today, which means every
deploy renders and pushes. That is wanted in production and not in
development, where a deploy is someone shipping code rather than asking for
the wiki to be rebuilt. A bare invocation is therefore gated by
:func:`_auto_run_enabled`; naming ``export`` is an explicit request and always
runs.

It used to register a Prefect deployment and serve it forever. The
``serve()`` loop and the ``wiki_curator_router`` dispatcher are gone —
see :mod:`wiki_curator_cog.flow` for what replaced each Prefect facility.
What is left here is the shell around one run: observability, the run id,
and the exit code.

**The exit code is load-bearing.** Railway will not start this service
again while a previous start is still ``Active``, so a run that neither
finishes nor fails takes every later run with it, silently. The deadline
in :mod:`wiki_curator_cog._deadline` stops that from inside; this module
makes sure the outcome reaches the outside as a non-zero exit and a
failed Healthchecks ping.

Observability layers:
  L1 — Healthchecks.io: start, then success or failure, per run
  L2 — Structured logs: mini_app_polis logger throughout
  L3 — Sentry: captures the unhandled exception that ends a run
  L4 — Run report: one per run, sent by ``flow.export_run``
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from mini_app_polis.environment import (
    Effect,
    Environment,
    current_environment,
    effect_enabled,
)

from wiki_curator_cog.boot import mask_url
from wiki_curator_cog.config import Config, load_config
from wiki_curator_cog.flow import REPO, export_run

load_dotenv()

LOG = log.get_logger()

__all__ = ["REPO", "main"]


def _ping_healthchecks(url: str, suffix: str = "") -> None:
    """Tell Healthchecks.io a run started, succeeded or failed.

    Three pings per run rather than one on boot. A boot ping said the
    process had started, which for a one-shot is the least interesting
    moment in the run: it is green whether or not the export then hung
    for an hour. ``/start`` followed by a success or ``/fail`` ping means
    the check's grace period catches the run that never came back — the
    one failure mode a process cannot report on its own behalf.

    One URL is one check across both environments. A dev container
    pinging it holds the production check green while production is dead,
    so the effect gate comes before the URL is read.
    """
    if not effect_enabled(Effect.HEALTHCHECKS):
        LOG.info("healthchecks.ping_suppressed reason=not_production")
        return
    if not url:
        return
    try:
        httpx.get(url.rstrip("/") + suffix, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — the ping is not the job
        LOG.warning("healthchecks.ping_failed suffix=%s err=%s", suffix or "/", exc)


def _init_observability(config: Config, run_id: str) -> None:
    if config.sentry_dsn:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            traces_sample_rate=0.0,
            environment=current_environment().value,
            release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
        )
    LOG.info(
        "wiki-curator-cog.boot env=%s version=%s run_id=%s api=%s wiki=%s "
        "branch=%s repo=%s budget=%ss",
        current_environment().value,
        os.getenv("RELEASE_VERSION", "dev"),
        run_id,
        config.kaiano_api_base_url,
        config.wiki_repo_path,
        config.wiki_branch,
        mask_url(config.wiki_repo_url),
        config.run_timeout_seconds,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-curator-cog",
        description=(
            "Render the wcs-wiki once and exit. Naming 'export' runs "
            "unconditionally; with no argument the run is gated by "
            "RUN_ON_START, which defaults to on in production only."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=["export"],
        help=(
            "Optional, and only one mode exists. Naming it asks for a run "
            "outright; omitting it defers to RUN_ON_START."
        ),
    )
    return parser.parse_args(argv)


def _auto_run_enabled() -> bool:
    """Whether starting the container should, by itself, rebuild the wiki.

    Production: yes. Starting the service *is* the trigger until the API
    owns the wake, so a deploy there is a run.

    Everywhere else: no. A development deploy is someone shipping code, and
    it would otherwise clone the real wcs-wiki, re-render the whole corpus
    and push — to a branch of the production repo, since there is no second
    wiki. Shipping twice in a row would mean rendering twice, for nothing.

    ``RUN_ON_START`` overrides in both directions, so a development run can
    be asked for without editing the start command.
    """
    raw = os.getenv("RUN_ON_START", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return current_environment() is Environment.PRODUCTION


def main(argv: list[str] | None = None) -> int:
    """Run one export and return the process exit code."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # Before load_config, so a container that is not going to do anything
    # exits cleanly whether or not its secrets are in place.
    if args.mode is None and not _auto_run_enabled():
        LOG.info(
            "export.skipped reason=auto_run_disabled env=%s "
            "hint=set RUN_ON_START=true, or pass the export argument",
            current_environment().value,
        )
        return 0

    # Minted here, not in the flow, so that the id is in the first log
    # line — the one a run that dies during config load still writes.
    run_id = str(uuid.uuid4())

    config = load_config()
    _init_observability(config, run_id)
    _ping_healthchecks(config.healthchecks_url, "/start")

    try:
        summary = export_run(run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — top-level guard
        # The run report has already been sent by export_run on its way
        # out. This adds the two things the report cannot: the exception
        # itself, to Sentry, and a non-zero exit, to Railway.
        sentry_sdk.capture_exception(exc)
        LOG.error("export.failed run_id=%s err=%s", run_id, exc)
        _ping_healthchecks(config.healthchecks_url, "/fail")
        return 1

    _ping_healthchecks(config.healthchecks_url)
    LOG.info("export.complete run_id=%s summary=%s", run_id, summary)
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
