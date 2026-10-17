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
    api_state,
    consolidate_local,
    export_run,
    generate_local,
    link_local,
    materialise_local,
    render_local,
    run_job,
    state_import_local,
    state_verify_local,
)
from wiki_curator_cog.units import JobError

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
            "job",
            "materialise",
        ],
        help=(
            "Optional. 'export' asks for a run outright; omitting it defers "
            "to RUN_ON_START. 'render' is the local proof-of-concept loop. "
            "'link' runs the linking pass into a local JSON file. "
            "'consolidate' decides which entities are facets of broader ones. "
            "'state-import' sends links.json, facets.json and prose.json to "
            "the API; 'state-verify' checks the API holds exactly what they do. "
            "'job' runs one worker job (--job JSON) as the wiki-jobs worker would. "
            "'materialise' builds page documents (every page, or --entity) "
            "and stores them in the API; no model call."
        ),
    )
    parser.add_argument(
        "--facets-file",
        default=None,
        help="consolidate: the facets file (default: facets.json in the state folder).",
    )
    parser.add_argument(
        "--facets",
        default=None,
        help=(
            "render / generate / link: the facets file (default: facets.json "
            "in the state folder)."
        ),
    )
    parser.add_argument(
        "--links-file",
        default=None,
        help="link: the links file (default: links.json in the state folder).",
    )
    parser.add_argument(
        "--links",
        default=None,
        help=(
            "render / generate / consolidate: the links file (default: "
            "links.json in the state folder)."
        ),
    )
    parser.add_argument(
        "--prose",
        default=None,
        help=(
            "render / consolidate: the prose file (default: prose.json in the "
            "state folder)."
        ),
    )
    parser.add_argument(
        "--prose-file",
        default=None,
        help="generate: the prose file (default: prose.json in the state folder).",
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
        help=(
            "generate / materialise: an entity slug to generate or "
            "materialise; repeat for several."
        ),
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
        "--job",
        default=None,
        help=(
            'job: one worker job as JSON, e.g. \'{"job": "link", "source_id": '
            '"..."}\', \'{"job": "generate", "entity": "anchor"}\' or '
            '\'{"job": "consolidate", "entities": ["..."]}\'.'
        ),
    )
    parser.add_argument(
        "--state",
        choices=["api", "files"],
        default="api",
        help=(
            "render / link / generate / consolidate: where links, facets and "
            "prose are read from and written to. 'api' (the default) reads "
            "the API and writes back what a run changed, keeping the files "
            "as a checked cache; 'files' uses the files alone."
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "The folder holding links.json, facets.json and prose.json, for "
            "every command that uses them. Default: $WIKI_REPO_PATH/.curator. "
            "A file flag (--links, --prose-file, ...) overrides one file."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "link / generate / consolidate: make no model calls and write "
            "nothing; print how many calls a real run would make, their "
            "tokens and an estimated cost. Needs no API key. materialise: "
            "build the documents and store none."
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


class NoStateFolderError(RuntimeError):
    """A command needs its state files and no folder says where they are."""


def _state_dir(args: argparse.Namespace) -> Path | None:
    """--state-dir, else $WIKI_REPO_PATH/.curator. Never the current folder.

    One folder for all three files, for every command: a default per file,
    relative to wherever the command was run, is how a generate run once
    started from an empty prose.json beside the code and regenerated the
    whole wiki.
    """
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    wiki = os.getenv("WIKI_REPO_PATH", "").strip()
    if wiki:
        return (Path(wiki).expanduser() / ".curator").resolve()
    return None


def _state_file(
    args: argparse.Namespace, explicit: str | None, name: str, *, required: bool
) -> Path | None:
    """One state file: the flag if given, else ``name`` in the state folder.

    ``required`` files are the ones the command writes: with no flag and no
    folder it stops rather than guess. An optional file that is not in the
    folder is left out when the state is files; with the API it is always
    used, the path being its cache.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    folder = _state_dir(args)
    if folder is None:
        if required:
            raise NoStateFolderError(
                f"no state folder for {name}: set WIKI_REPO_PATH (its .curator "
                "folder is used) or pass --state-dir"
            )
        return None
    path = folder / name
    if required or args.state == "api" or path.exists():
        return path
    return None


def _written_file(args: argparse.Namespace, explicit: str | None, name: str) -> Path:
    """The state file a command writes: the flag, else the state folder's."""
    path = _state_file(args, explicit, name, required=True)
    assert path is not None
    return path


def _store(args: argparse.Namespace) -> dict:
    """``state=`` for a local command: the API unless --state files."""
    return {"state": api_state()} if args.state == "api" else {}


def _render(
    wiki_repo_path_raw: str,
    links: Path | None = None,
    prose: Path | None = None,
    facets: Path | None = None,
    store: dict | None = None,
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
    summary = render_local(wiki_repo_path, links, prose, facets, **(store or {}))
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
        _written_file(args, args.links_file, "links.json"),
        facets_path=_state_file(args, args.facets, "facets.json", required=False),
        **kwargs,
        **_store(args),
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _generate(args: argparse.Namespace) -> int:
    """Local generation: overviews to a file, a summary to stdout."""
    wiki = os.getenv("WIKI_REPO_PATH", "").strip()
    summary = generate_local(
        _written_file(args, args.prose_file, "prose.json"),
        links_path=_state_file(args, args.links, "links.json", required=False),
        wiki_repo_path=Path(wiki).expanduser().resolve() if wiki else None,
        entities=args.entity,
        sample=args.sample,
        condense=args.condense,
        facets_path=_state_file(args, args.facets, "facets.json", required=False),
        dry_run=args.dry_run,
        **({"workers": args.workers} if args.workers is not None else {}),
        **_store(args),
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
    facets_file = _written_file(args, args.facets_file or args.facets, "facets.json")
    summary = consolidate_local(
        facets_file,
        prose_path=_state_file(args, args.prose, "prose.json", required=False),
        links_path=_state_file(args, args.links, "links.json", required=False),
        **kwargs,
        **_store(args),
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _job(args: argparse.Namespace) -> int:
    """One worker job, locally: its result and the jobs that would follow."""
    try:
        job = json.loads(args.job or "")
    except json.JSONDecodeError:
        LOG.error("job.failed err=--job must be a JSON object")
        return 2
    state_dir = _state_dir(args)
    if state_dir is None:
        raise NoStateFolderError(
            "no state folder: set WIKI_REPO_PATH (its .curator folder) or pass "
            "--state-dir"
        )
    wiki = os.getenv("WIKI_REPO_PATH", "").strip()
    try:
        summary = run_job(
            job,
            state_dir,
            wiki_repo_path=Path(wiki).expanduser().resolve() if wiki else None,
            dry_run=args.dry_run,
            **_store(args),
        )
    except JobError as exc:
        LOG.error("job.failed err=%s", exc)
        return 2
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _materialise(args: argparse.Namespace) -> int:
    """Page documents for every page, or the named ones, into the API."""
    state_dir = _state_dir(args)
    if state_dir is None:
        raise NoStateFolderError(
            "no state folder: set WIKI_REPO_PATH (its .curator folder) or pass "
            "--state-dir"
        )
    summary = materialise_local(
        state_dir, entities=args.entity, dry_run=args.dry_run, **_store(args)
    )
    sys.stdout.write(json.dumps(summary, default=str, indent=2) + "\n")
    return 0


def _state(args: argparse.Namespace) -> int:
    """Import the state files into the API, or verify the API holds them."""
    state_dir = _state_dir(args)
    if state_dir is None:
        LOG.error(
            "state.failed err=set WIKI_REPO_PATH (its .curator folder) or pass "
            "--state-dir"
        )
        return 2
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
        try:
            return _render(
                os.getenv("WIKI_REPO_PATH", ""),
                _state_file(args, args.links, "links.json", required=False),
                _state_file(args, args.prose, "prose.json", required=False),
                _state_file(args, args.facets, "facets.json", required=False),
                _store(args),
            )
        except NoStateFolderError as exc:
            LOG.error("render.failed err=%s", exc)
            return 2
    try:
        if args.mode == "link":
            return _link(args)
        if args.mode == "generate":
            return _generate(args)
        if args.mode == "consolidate":
            return _consolidate(args)
        if args.mode == "job":
            return _job(args)
        if args.mode == "materialise":
            return _materialise(args)
    except NoStateFolderError as exc:
        LOG.error("%s.failed err=%s", args.mode, exc)
        return 2
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
