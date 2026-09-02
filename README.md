# wiki-curator-cog

[![CI](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/wiki-curator-cog?label=version)](https://github.com/mini-app-polis/wiki-curator-cog/releases)

Prefect pipeline cog that maintains the [`wcs-wiki`](https://github.com/mini-app-polis/wcs-wiki) markdown knowledge base.

Reads structured WCS notes from `api-kaianolevine-com` (HTTP API, read-only), deterministically routes their content onto concept / technique / instructor / terminology pages in a local clone of `wcs-wiki`, and pushes to GitHub. The curator does not call any LLM at runtime — upstream `notes-ingest-cog` has already extracted the structured `notes_json` from raw transcripts. The schema this cog implements is defined in `wcs-wiki/CLAUDE.md`; the strategic intent and queued work live in `wcs-wiki/ROADMAP.md`.

| Mode (`mode=…`) | What it does                                                              | Source                         | Sink                |
| --------------- | ------------------------------------------------------------------------- | ------------------------------ | ------------------- |
| `backfill`      | One-time corpus run: process every existing note in chronological order   | `api-kaianolevine-com` (`/v1/wcs/notes/all`) | `wcs-wiki` repo     |
| `incremental`   | Process notes added since the last run (steady-state, post-Phase-2)       | `api-kaianolevine-com` (`/v1/wcs/notes/all?since=…`) | `wcs-wiki` repo     |

Triggered by `watcher-cog` downstream of `transcription-cog` completion (incremental), or manually via Prefect UI (backfill).

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
- An Anthropic API key
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

### Run a backfill locally

```bash
uv run python -m wiki_curator_cog.main --mode backfill
```

---

## Observability

Three layers, matching ecosystem convention:

1. **Healthchecks.io** — pinged on startup (liveness signal).
2. **Structured logs** — `mini_app_polis` logger throughout, JSON output via `common-python-utils`.
3. **Sentry** — unhandled exceptions and warnings on schema-violation findings.

Pipeline-evaluation findings (a fourth signal) are emitted to `pipeline_evaluations` per-run, surfaced in the existing pipeline-evaluations UI on `website-astro-software`.

---

## Key concepts

**The curator does not invent content.** Every wiki claim traces to an upstream source. The schema in `wcs-wiki/CLAUDE.md` is the operating manual; this cog implements it.

**Deterministic by design.** No LLM calls at runtime. Given the same upstream notes_json and the same alias maps, the same input produces the same output. The only LLM step in the pipeline is upstream in `notes-ingest-cog`.

**Idempotency.** In incremental mode, ingesting the same `note_id` at the same curator version is a no-op. Backfill mode bypasses this — it wipes the derived layer (concepts, techniques, instructors, terminology) at start of run and rebuilds from scratch so behavior changes to the curator produce a clean result.

**Curator version.** Auto-derived at runtime from the package's `pyproject.toml`. semantic-release bumps the version on push to main based on conventional commits: `feat:` → minor, `fix:` → patch, `BREAKING CHANGE:` → major. `chore:` / `docs:` / `style:` commits do not produce a release.

**Single-instance concurrency.** Two concurrent runs against the same wiki repo would race on git operations and inventory consistency. Configure a Prefect concurrency slot of 1.

**Read-only against upstream.** The curator only calls `GET` endpoints for note content. The only `POST` is to `/v1/evaluations` for pipeline-evaluation findings.
