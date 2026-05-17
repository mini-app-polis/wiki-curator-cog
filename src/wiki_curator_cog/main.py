"""Application entrypoint for wiki-curator-cog.

Two invocation styles:

  ``python -m wiki_curator_cog.main``
      Default. Registers the router-style Prefect deployment
      (``wiki-curator-cog/wiki-curator-cog``) and starts a runner loop
      that polls for scheduled or manually triggered runs.
      This is the steady-state Railway start command.

  ``python -m wiki_curator_cog.main backfill`` (or ``incremental``)
      One-off mode. Initializes observability, runs the corresponding
      flow synchronously in-process, prints the summary dict, and
      exits with status 0 (or 1 on failure). Useful for the Phase 1
      backfill where no Prefect Cloud trigger is wired up yet, or for
      developer machines that don't want to run a Prefect serve loop.

The router flow itself supports the same modes when triggered via
Prefect Cloud:

  - ``"backfill"``     → one-time corpus run
  - ``"incremental"``  → process notes since last run

Observability layers:
  L1 — Healthchecks.io: pinged on startup
  L2 — Structured logs: mini_app_polis logger throughout
  L3 — Sentry: captures all unhandled exceptions
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Literal

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, serve

from wiki_curator_cog.boot import mask_url
from wiki_curator_cog.config import load_config
from wiki_curator_cog.flow import backfill_flow, incremental_flow

load_dotenv()

LOG = log.get_logger()


#: Supported router modes. Declared as a Literal so Prefect Cloud's
#: "Custom Run" UI renders a dropdown (via the auto-generated JSON-schema
#: enum) instead of a free-form string field.
WikiCuratorMode = Literal["backfill", "incremental"]

_MODE_DISPATCH: dict[str, Any] = {
    "backfill": backfill_flow,
    "incremental": incremental_flow,
}


@flow(name="wiki-curator-cog")
def wiki_curator_router(mode: WikiCuratorMode) -> Any:
    """Single entrypoint flow that dispatches to a sub-flow by ``mode``."""
    target = _MODE_DISPATCH.get(mode)
    if target is None:
        raise ValueError(f"Unknown mode {mode!r}. Supported: {sorted(_MODE_DISPATCH)}")
    return target()


def _ping_healthchecks(url: str) -> None:
    if not url:
        return
    try:
        httpx.get(url, timeout=5.0)
    except Exception as exc:
        LOG.warning("healthchecks.ping_failed err=%s", exc)


def _init_observability(config) -> None:  # noqa: ANN001
    if config.sentry_dsn:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            traces_sample_rate=0.0,
            environment=os.getenv("RAILWAY_ENVIRONMENT", "production"),
            release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
        )
    _ping_healthchecks(config.healthchecks_url)
    LOG.info(
        "wiki-curator-cog.boot version=%s curator_version=%s api=%s "
        "wiki=%s branch=%s repo=%s",
        os.getenv("RELEASE_VERSION", "dev"),
        config.curator_version,
        config.kaiano_api_base_url,
        config.wiki_repo_path,
        config.wiki_branch,
        mask_url(config.wiki_repo_url),
    )


def _serve_forever() -> int:
    """Default Railway start command: register the deployment and serve."""
    config = load_config()
    _init_observability(config)

    deployment = wiki_curator_router.to_deployment(name="wiki-curator-cog")
    serve(deployment)
    return 0


def _run_one_off(mode: str) -> int:
    """One-off invocation: run a flow synchronously and exit.

    Used for the Phase 1 backfill on Railway (override start command
    to ``python -m wiki_curator_cog.main backfill``) and for developer
    machines triggering an ad-hoc run.
    """
    if mode not in _MODE_DISPATCH:
        LOG.error("one_off.unknown_mode mode=%s", mode)
        sys.stderr.write(
            f"Unknown mode {mode!r}. Supported: {sorted(_MODE_DISPATCH)}\n"
        )
        return 2

    config = load_config()
    _init_observability(config)

    target = _MODE_DISPATCH[mode]
    LOG.info("one_off.start mode=%s", mode)
    try:
        summary = target()
    except Exception as exc:  # noqa: BLE001 — top-level guard
        sentry_sdk.capture_exception(exc)
        LOG.error("one_off.failed mode=%s err=%s", mode, exc)
        return 1

    # Pretty-print the summary so Railway logs (and developers) see it.
    LOG.info("one_off.complete mode=%s summary=%s", mode, summary)
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-curator-cog",
        description=(
            "Run wiki-curator-cog. With no subcommand, registers a Prefect "
            "deployment and serves forever (Railway default). With "
            "'backfill' or 'incremental', runs that flow once and exits."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=sorted(_MODE_DISPATCH),
        help=(
            "One-off mode to run. Omit to serve the Prefect deployment "
            "in the steady-state pattern."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode is None:
        return _serve_forever()
    return _run_one_off(args.mode)


if __name__ == "__main__":
    sys.exit(main())
