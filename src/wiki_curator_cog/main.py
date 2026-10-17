"""Application entrypoint for wiki-curator-cog.

One invocation is one export. The process renders the wiki once and
exits; there is no resident loop and nothing to dispatch.

    python -m wiki_curator_cog.main          # what Railway's start command runs
    python -m wiki_curator_cog.main export   # a run asked for on purpose
    python -m wiki_curator_cog.main render   # local only: no commit, push or report
    python -m wiki_curator_cog.main link --sample 20   # local linking pass to a file

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
from pathlib import Path

import sentry_sdk
from mini_app_polis import logger as log
from mini_app_polis.environment import Environment, current_environment

from wiki_curator_cog.boot import mask_url
from wiki_curator_cog.config import Config, load_config
from wiki_curator_cog.flow import (
    REPO,
    consolidate_local,
    export_run,
    generate_local,
    link_local,
    render_local,
    state_import_local,
    state_verify_local,
)

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
            "RUN_ON_START, which defaults to on in production only. "
            "'render' writes into the existing clone at WIKI_REPO_PATH and "
            "stops there: no commit, no push, no run report."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=[
            "export",
            "render",
            "link",
            "generate",
            "consolidate",
            "state-import",
            "state-verify",
        ],
        help=(
            "Optional. 'export' asks for a run outright; omitting it defers "
            "to RUN_ON_START. 'render' is the local proof-of-concept loop. "
            "'link' runs the linking pass into a local JSON file. "
            "'consolidate' decides which entities are facets of broader ones. "
            "'state-import' sends links.json, facets.json and prose.json to "
            "the API; 'state-verify' checks the API holds exactly what they do."
        ),
    )
    parser.add_argument(
        "--facets-file",
        default="facets.json",
        help="consolidate: the facet decisions, and the watermark (default facets.json).",
    )
    parser.add_argument(
        "--facets",
        default=None,
        help=(
            "render / generate / link: the facets file from `consolidate`. "
            "Default: facets.json next to the --links file, when it exists."
        ),
    )
    parser.add_argument(
        "--links-file",
        default="links.json",
        help="link: the references file, and the watermark (default links.json).",
    )
    parser.add_argument(
        "--links",
        default=None,
        help=(
            "render / generate: the links file from `link` — overlaid on the "
            "render; used by generate for proposed concepts and referring "
            "statements."
        ),
    )
    parser.add_argument(
        "--prose",
        default=None,
        help=(
            "render: put the overviews from this prose file (from `generate`) on "
            "the pages; consolidate: show the model each entity's definition."
        ),
    )
    parser.add_argument(
        "--prose-file",
        default="prose.json",
        help="generate: the overviews file, and the watermark (default prose.json).",
    )
    parser.add_argument(
        "--condense",
        action="store_true",
        help="generate: only rewrite pages whose prose has outgrown the fixed shape.",
    )
    parser.add_argument(
        "--entity",
        action="append",
        default=None,
        help="generate: an entity slug to generate; repeat for several.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help=(
            "link: a reproducible sample of N statements; generate: N entities; "
            "consolidate: the first N undecided entities."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "link / generate / consolidate: model calls in flight at once "
            "(default: 1 for link, 4 for generate, 8 for consolidate)."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="link / consolidate: statements or entities per LLM call.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "state-import / state-verify: the folder holding links.json, "
            "facets.json and prose.json (wcs-wiki/.curator)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "link / generate / consolidate: make no model calls and write "
            "nothing; print how many calls a real run would make, their "
            "tokens and an estimated cost. Needs no API key."
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


def _facets_path(facets: str | None, links: str | None) -> Path | None:
    """--facets, else facets.json beside the links file if it is there.

    Picking it up by default keeps render, generate and link on the same
    folds: a generate run that forgot the flag would otherwise rewrite every
    facet as its own page again.
    """
    if facets:
        return Path(facets).expanduser().resolve()
    if links:
        sibling = Path(links).expanduser().resolve().with_name("facets.json")
        if sibling.exists():
            return sibling
    return None


def _render(
    wiki_repo_path_raw: str,
    links: str | None = None,
    prose: str | None = None,
    facets: str | None = None,
) -> int:
    """The local loop: render into a clone, print what it found.

    Exits non-zero when any link has no page, after writing, so the result
    is still there to inspect.
    """
    if not wiki_repo_path_raw.strip():
        LOG.error(
            "render.failed err=WIKI_REPO_PATH must point at a local wcs-wiki clone"
        )
        return 2
    wiki_repo_path = Path(wiki_repo_path_raw).expanduser().resolve()
    links_path = Path(links).expanduser().resolve() if links else None
    prose_path = Path(prose).expanduser().resolve() if prose else None
    summary = render_local(
        wiki_repo_path, links_path, prose_path, _facets_path(facets, links)
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 1 if summary["broken_links"]["count"] else 0


def _link(args: argparse.Namespace) -> int:
    """The local linking pass: references to a file, a summary to stdout."""
    kwargs: dict = {"sample": args.sample, "dry_run": args.dry_run}
    if args.batch_size is not None:
        kwargs["batch_size"] = args.batch_size
    if args.workers is not None:
        kwargs["workers"] = args.workers
    summary = link_local(
        Path(args.links_file).expanduser().resolve(),
        facets_path=_facets_path(args.facets, args.links_file),
        **kwargs,
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _generate(args: argparse.Namespace) -> int:
    """Local generation: overviews to a file, a summary to stdout."""
    wiki = os.getenv("WIKI_REPO_PATH", "").strip()
    links = args.links or "links.json"
    summary = generate_local(
        Path(args.prose_file).expanduser().resolve(),
        links_path=Path(links).expanduser().resolve(),
        wiki_repo_path=Path(wiki).expanduser().resolve() if wiki else None,
        entities=args.entity,
        sample=args.sample,
        condense=args.condense,
        facets_path=_facets_path(args.facets, links),
        dry_run=args.dry_run,
        **({"workers": args.workers} if args.workers is not None else {}),
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _consolidate(args: argparse.Namespace) -> int:
    """Local facet consolidation: decisions to a file, a summary to stdout."""
    kwargs: dict = {"sample": args.sample, "dry_run": args.dry_run}
    if args.batch_size is not None:
        kwargs["batch_size"] = args.batch_size
    if args.workers is not None:
        kwargs["workers"] = args.workers
    facets_file = Path(args.facets_file).expanduser().resolve()
    links = (
        Path(args.links).expanduser().resolve()
        if args.links
        else facets_file.with_name("links.json")
    )
    summary = consolidate_local(
        facets_file,
        prose_path=Path(args.prose).expanduser().resolve() if args.prose else None,
        links_path=links if links.exists() else None,
        **kwargs,
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _state(args: argparse.Namespace) -> int:
    """Import the state files into the API, or verify the API holds them."""
    if not args.state_dir:
        LOG.error("state.failed err=--state-dir is required (wcs-wiki/.curator)")
        return 2
    state_dir = Path(args.state_dir).expanduser().resolve()
    if args.mode == "state-import":
        kwargs = {"chunk": args.batch_size} if args.batch_size else {}
        summary = state_import_local(state_dir, **kwargs)
        sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
        return 0
    summary = state_verify_local(state_dir)
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0 if summary["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    """Run one export, a local render or a local linking pass; return the exit code."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.mode == "render":
        return _render(
            os.getenv("WIKI_REPO_PATH", ""), args.links, args.prose, args.facets
        )
    if args.mode == "link":
        return _link(args)
    if args.mode == "generate":
        return _generate(args)
    if args.mode == "consolidate":
        return _consolidate(args)
    if args.mode in {"state-import", "state-verify"}:
        return _state(args)

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
