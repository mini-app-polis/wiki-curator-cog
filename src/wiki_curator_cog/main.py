"""Application entrypoint for wiki-curator-cog.

Registers a single router-style Prefect deployment
(``wiki-curator-cog/wiki-curator-cog``) and starts a runner loop that
polls for scheduled or manually triggered runs.

The router flow dispatches to one of the underlying production flows
based on the ``mode`` parameter.

Supported modes:
    - "backfill"      → one-time corpus run
    - "incremental"   → process notes since last run

Railway start command: python -m wiki_curator_cog.main

Observability layers:
  L1 — Healthchecks.io: pinged on startup
  L2 — Structured logs: mini_app_polis logger throughout
  L3 — Sentry: captures all unhandled exceptions
"""

from __future__ import annotations

import os
import sys
from typing import Any, Literal

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, serve

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


def main() -> int:
    """Boot the cog: init observability, then serve the router flow."""
    config = load_config()

    if config.sentry_dsn:
        sentry_sdk.init(dsn=config.sentry_dsn, traces_sample_rate=0.0)

    _ping_healthchecks(config.healthchecks_url)

    LOG.info(
        "wiki-curator-cog.boot version=%s curator_version=%s api=%s wiki=%s",
        os.getenv("RELEASE_VERSION", "dev"),
        config.curator_version,
        config.kaiano_api_base_url,
        config.wiki_repo_path,
    )

    deployment = wiki_curator_router.to_deployment(name="wiki-curator-cog")
    serve(deployment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
