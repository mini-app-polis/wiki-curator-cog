"""Application entrypoint for wiki-curator-cog.

Two invocation styles:

  ``python -m wiki_curator_cog.main``
      Default. Registers the Prefect deployment and starts a runner loop.

  ``python -m wiki_curator_cog.main export``
      One-off mode. Runs export_flow synchronously and exits.

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
from typing import Any

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, serve

from wiki_curator_cog.boot import mask_url
from wiki_curator_cog.config import load_config
from wiki_curator_cog.flow import export_flow

load_dotenv()

LOG = log.get_logger()


@flow(name="wiki-curator-cog")
def wiki_curator_router() -> Any:
    """Single entrypoint flow that runs the export renderer."""
    return export_flow()


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
        "wiki-curator-cog.boot version=%s api=%s wiki=%s branch=%s repo=%s",
        os.getenv("RELEASE_VERSION", "dev"),
        config.kaiano_api_base_url,
        config.wiki_repo_path,
        config.wiki_branch,
        mask_url(config.wiki_repo_url),
    )


def _serve_forever() -> int:
    config = load_config()
    _init_observability(config)
    deployment = wiki_curator_router.to_deployment(name="wiki-curator-cog")
    serve(deployment)  # type: ignore[arg-type]
    return 0


def _run_one_off() -> int:
    config = load_config()
    _init_observability(config)
    LOG.info("one_off.start mode=export")
    try:
        summary = export_flow()
    except Exception as exc:  # noqa: BLE001 — top-level guard
        sentry_sdk.capture_exception(exc)
        LOG.error("one_off.failed mode=export err=%s", exc)
        return 1

    LOG.info("one_off.complete mode=export summary=%s", summary)
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-curator-cog",
        description=(
            "Run wiki-curator-cog. With no subcommand, registers a Prefect "
            "deployment and serves forever. With 'export', runs one render "
            "and exits."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=["export"],
        help="Subcommand to run. Omit to serve the Prefect deployment.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode is None:
        return _serve_forever()
    return _run_one_off()


if __name__ == "__main__":
    sys.exit(main())
