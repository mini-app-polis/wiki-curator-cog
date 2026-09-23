# wiki-curator-cog

[![CI](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/wiki-curator-cog?label=version)](https://github.com/mini-app-polis/wiki-curator-cog/releases)

Renders the [`wcs-wiki`](https://github.com/mini-app-polis/wcs-wiki) markdown knowledge base from the canonical WCS corpus in `api-kaianolevine-com`.

Reads the canonical WCS entity graph from `api-kaianolevine-com` in one call (`GET /v1/wcs/wiki/export`, read-only), deterministically renders it onto source / concept / technique / instructor / terminology pages and views in a clone of `wcs-wiki`, and pushes to GitHub. The curator does not call any LLM at runtime — upstream `notes-ingest-cog` has already extracted the structured `notes_json` from raw transcripts. The schema this cog implements is defined in `wcs-wiki/CLAUDE.md`; the strategic intent and queued work live in `wcs-wiki/ROADMAP.md`.

**One mode, `export`.** A run takes no arguments: it fetches the whole corpus,
re-renders every page from it, removes derived pages that are no longer
produced, commits what changed and pushes. Running it twice over unchanged
upstream data produces the same bundle and the second run commits nothing —
which is why the trigger is allowed to be crude. The three-mode split this
README used to document was retired by ADR-002 and ADR-004.

Triggered manually. One wake is one run: the process renders once and exits. The direction of travel is a run per source change, asked for by `api-kaianolevine-com`; nothing is built for that yet (ADR-004).

See `docs/PIPELINE.md` for the ecosystem flow diagram and `docs/decisions/` for ADRs.

---

## Architecture

```
Drive transcript drop
        │
        ▼
transcription-cog ──► raw transcripts
        │
        ▼
notes-ingest-cog (LLM extraction) ──► api-kaianolevine-com (structured notes)
                                            │
                                            ▼ (HTTP API, read-only)
                                    wiki-curator-cog
                                            │
                                            ▼
                                      wcs-wiki repo
```

The curator never accesses the database directly. All upstream data is fetched over HTTP from `api-kaianolevine-com`, authenticated with this cog's own named machine key.

Pipeline-evaluation findings (quality signals, judgment-call records) are emitted to `pipeline_evaluations` via `POST /v1/evaluations` — same pattern as every other cog.

---

## Running locally

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- A `.env` file populated from `.env.example`
- A local clone of `wcs-wiki`
- `WIKI_CURATOR_COG_API_KEY` — this cog's own named key. The API grants it the
  `corpus-reader` and `pipeline-writer` roles; there is no fallback credential.

### Setup

```bash
git clone git@github.com:mini-app-polis/wiki-curator-cog.git
cd wiki-curator-cog
uv sync --all-extras
uv run pre-commit install
uv run pre-commit run --all-files
cp .env.example .env
# populate .env with your values, including WIKI_REPO_PATH pointing at your wcs-wiki clone
```

### Run tests

```bash
uv run pytest
```

### Run an export locally

```bash
uv run python -m wiki_curator_cog.main export
```

---

## Observability

Four layers, matching ecosystem convention:

1. **Healthchecks.io** — `/start` at the beginning of a run, then success or
   `/fail` at the end. Per run rather than on boot: a boot ping went green
   whether or not the export then hung for an hour, and the check's grace
   period is what catches a run that never came back at all.
2. **Structured logs** — `mini_app_polis` logger throughout, JSON output via `common-python-utils`.
3. **Sentry** — the unhandled exception that ends a run.
4. **Run report** — one per run, sent however it ends: SUCCESS or WARN out of a
   good run, ERROR out of a failed one.

Pipeline-evaluation findings are emitted to `pipeline_evaluations` per run, surfaced in the existing pipeline-evaluations UI on `website-astro-software`.

---

## Key concepts

**The curator does not invent content.** Every wiki claim traces to an upstream source. The schema in `wcs-wiki/CLAUDE.md` is the operating manual; this cog implements it.

**Deterministic by design.** No LLM calls at runtime. Given the same upstream notes_json and the same alias maps, the same input produces the same output. The only LLM step in the pipeline is upstream in `notes-ingest-cog`.

**Idempotency.** The render is a pure function of the export it fetched: every page is rewritten each run, derived pages no longer produced are removed, and an unchanged corpus yields an unchanged tree with nothing to commit. There is no per-item skip to get wrong, which is what lets a redundant run cost time rather than correctness.

**Curator version.** Auto-derived at runtime from the package's `pyproject.toml`. semantic-release bumps the version on push to main based on conventional commits: `feat:` → minor, `fix:` → patch, `BREAKING CHANGE:` → major. `chore:` / `docs:` / `style:` commits do not produce a release.

**Single-instance concurrency.** Each run is its own process with its own clone, so there is no shared working tree to race on, and Railway will not start the service again while a previous start is `Active`. Two overlapping runs could still race on the push, where git rejects the loser non-fast-forward. The Prefect concurrency slot this used to require is gone (ADR-004).

**Read-only against upstream.** The curator only calls `GET` endpoints for note content. The only `POST` is to `/v1/evaluations` for pipeline-evaluation findings.
