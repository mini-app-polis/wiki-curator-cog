"""Command-line entrypoint for wiki-curator-cog's local commands.

    python -m wiki_curator_cog.main render       # the markdown export, into WIKI_REPO_PATH
    python -m wiki_curator_cog.main link --sample 20
    python -m wiki_curator_cog.main materialise  # page documents into the API

Each command runs once against the API the environment points at
(production under ``doppler run --config prd``) and prints a JSON summary.
The deployed curator is the wiki-jobs worker (:mod:`wiki_curator_cog.worker`,
ADR-006); nothing here is deployed. The Railway ``export`` mode was retired
with its service (MINI-95).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mini_app_polis import logger as log

from wiki_curator_cog.flow import (
    REPO,
    api_state,
    consolidate_local,
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-curator-cog",
        description=(
            "The curator's local commands, run once against the API. "
            "'render' writes the markdown export into the wcs-wiki clone at "
            "WIKI_REPO_PATH: no commit, no push."
        ),
    )
    parser.add_argument(
        "mode",
        choices=[
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
            "'render' writes the markdown export from the page documents. "
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
            "materialise; repeat for several. With generate --condense, only "
            "these are rewritten (if over the shape), to try it on a sample."
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


def _rebuild_pages(
    args: argparse.Namespace, summary: dict, slugs: list[str] | None
) -> None:
    """Rebuild the pages a local run changed, as the worker's jobs do.

    The worker's link, consolidate and generate jobs each end by
    materialising their pages; a local run against the API does the same
    here, so nothing waits on /admin/wiki's "Rebuild every page". ``slugs``
    None is every page (a link or consolidate run can touch any of them); an
    empty list rebuilds nothing. A dry run, or one against the files alone,
    stores no pages. The result is added to ``summary`` as ``pages_rebuilt``.
    """
    if args.dry_run or args.state != "api" or slugs == []:
        return
    state_dir = _state_dir(args)
    if state_dir is None:
        LOG.warning("rebuild.skipped err=no state folder; rebuild the pages by hand")
        return
    summary["pages_rebuilt"] = materialise_local(
        state_dir, entities=slugs, **_store(args)
    )


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
    _rebuild_pages(args, summary, None if summary.get("linked") else [])
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
    changed = summary.get("slugs" if args.condense else "generated_slugs")
    _rebuild_pages(args, summary, list(changed or []))
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
    _rebuild_pages(args, summary, None if summary.get("decided") else [])
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
    """Run one local command; return the exit code."""
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
    return _state(args)


if __name__ == "__main__":
    sys.exit(main())
