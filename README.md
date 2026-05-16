# wiki-curator-cog

[![CI](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/wiki-curator-cog?label=version)](https://github.com/mini-app-polis/wiki-curator-cog/releases)

Prefect pipeline cog that maintains the [`wcs-wiki`](https://github.com/mini-app-polis/wcs-wiki) markdown knowledge base.

Reads structured WCS notes from `api-kaianolevine-com` (HTTP API, read-only) and writes/updates markdown files in a local clone of `wcs-wiki`, then pushes to GitHub. The curator operates according to the schema defined in `wcs-wiki/CLAUDE.md` — that file is the operating manual; this repo is the runtime.

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
transcription-cog ──► api-kaianolevine-com (structured notes)
                              │
                              ▼ (HTTP API, read-only)
                      wiki-curator-cog
                              │
                              ▼
                        wcs-wiki repo
```

The curator never accesses the database directly. All upstream data is fetched via Clerk M2M-authenticated HTTP calls to `api-kaianolevine-com`.

Pipeline-evaluation findings (quality signals, judgment-call records) are emitted to `pipeline_evaluations` via `POST /v1/evaluations` — same pattern as every other cog.

---

## Running locally

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- A `.env` file populated from `.env.example`
- A local clone of `wcs-wiki`
- An Anthropic API key
- A Clerk M2M machine secret with `wcs_admin` scope

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

**Idempotency.** Ingesting the same `note_id` at the same `WIKI_CURATOR_VERSION` is a no-op. Bumping the curator version triggers reprocessing for affected source pages.

**Single-instance concurrency.** Two concurrent runs against the same wiki repo would race on git and inventory. Configure a Prefect concurrency slot of 1.

**Read-only against upstream.** The curator only calls `GET` endpoints for note content. The only `POST` is to `/v1/evaluations` for findings.
