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

This module is the shell around one run: observability, the run id,
and the exit code. The run itself is :func:`wiki_curator_cog.flow.export_run`.

**The exit code is load-bearing.** Railway will not start this service
again while a previous start is still ``Active``, so a run that neither
finishes nor fails takes every later run with it, silently. The deadline
in :mod:`wiki_curator_cog._deadline` stops that from inside; this module
makes sure the outcome reaches the outside as a non-zero exit.

Observability layers:
  L1 — Structured logs: mini_app_polis logger throughout
  L2 — Sentry: captures the unhandled exception that ends a run
  L3 — Run report: one per run, sent by ``flow.export_run``
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import sentry_sdk
from mini_app_polis import logger as log
from mini_app_polis.environment import Environment, current_environment

from wiki_curator_cog.boot import mask_url
from wiki_curator_cog.config import Config, load_config
from wiki_curator_cog.flow import REPO, export_run

LOG = log.get_logger()

__all__ = ["REPO", "main"]


def _init_observability(config: Config, run_id: str) -> None:
    if config.sentry_dsn:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            traces_sample_rate=0.0,
            environment=current_environment().value,
            release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
        )
        sentry_sdk.get_global_scope().set_tag("service", "wiki-curator-cog")
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

    try:
        summary = export_run(run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — top-level guard
        # The run report has already been sent by export_run on its way
        # out. This adds the two things the report cannot: the exception
        # itself, to Sentry, and a non-zero exit, to Railway.
        sentry_sdk.capture_exception(exc)
        LOG.error("export.failed run_id=%s err=%s", run_id, exc)
        return 1

    LOG.info("export.complete run_id=%s summary=%s", run_id, summary)
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
