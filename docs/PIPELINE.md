# Pipeline architecture

`wiki-curator-cog` consumes structured WCS notes from `api-kaianolevine-com` (HTTP API, read-only) and produces a markdown knowledge base in the `wcs-wiki` repo. The notes themselves are produced by `notes-ingest-cog` running an LLM-based extraction over raw transcripts; the curator runs no LLM at runtime and processes the already-structured `notes_json` deterministically.

## Flow diagram

```
Drive transcript drop
        │
        ▼
┌──────────────────────────┐
│   transcription-cog       │
│   raw transcripts         │
└──────────┬────────────────┘
           │ POST /v1/wcs/transcripts
           ▼
┌──────────────────────────┐
│   notes-ingest-cog        │
│   LLM extraction          │
└──────────┬────────────────┘
           │ POST /v1/wcs/notes
           ▼
┌──────────────────────────────────────────────┐
│   api-kaianolevine-com                       │
│   wcs_transcripts + wcs_notes (Postgres)     │
└──────────┬───────────────────────────────────┘
           │ GET /v1/wcs/notes/all (read-only, Clerk M2M)
           ▼
┌──────────────────────────┐
│   wiki-curator-cog        │  (this repo)
│   backfill | incremental  │  (deterministic, no LLM)
└──────────┬────────────────┘
           │ git commit / push
           ▼
┌──────────────────────────┐
│   wcs-wiki repo           │
│   (markdown knowledge base)│
└──────────────────────────┘
           │
           ▼
       Obsidian / GitHub web
```

## Two-layer wiki architecture

Per `wcs-wiki/CLAUDE.md`, the wiki is produced by two layers operating against the same repo:

- **Layer 1 — deterministic collection.** This cog. Reads `notes_json` from the API and routes its fields onto wiki pages mechanically via three alias maps (`instructors/`, `concepts/`, `techniques/` _aliases.yaml). Owns source pages, the `## By teacher` paragraphs, the `## Sources` / `## Referenced by` bullets, the index, the log, the views. Given the same upstream notes and the same alias maps, the same input always produces the same output.
- **Layer 2 — LLM polish pass.** Operates on Layer 1's output and refines it: prose synthesis where Layer 1 emits a mechanical Overview template, instructor-page `## Background` / `## Teaching themes` / `## Notable framings`, terminology-page creation when vocabulary collapses are ambiguous, lint findings, query answering. Runs in the same Prefect concurrency slot, after Layer 1 finishes.

This cog implements Layer 1.

## Auth

- Curator authenticates to `api-kaianolevine-com` via Clerk M2M JWT (Project Keystone). Machine secret in Doppler.
- `wcs_admin` scope required (curator reads all notes regardless of `is_default_visible` or `visibility`).
- GitHub fine-grained PAT scoped to `wcs-wiki` only; passed as `GH_TOKEN` in env. Used to clone and push.
- No database credentials. The curator never touches the database directly.

## Observability

| Layer | Signal              | Where                                             |
| ----- | ------------------- | ------------------------------------------------- |
| L1    | Liveness            | Healthchecks.io ping on startup                   |
| L2    | Structured logs     | `mini_app_polis` JSON logger                      |
| L3    | Unhandled exceptions| Sentry                                            |
| L4    | Quality signals     | `pipeline_evaluations` via `POST /v1/evaluations` |

## Concurrency

Single-instance Prefect flow (concurrency slot of 1). Two concurrent runs would race on git operations and wiki state. The `concurrency("wiki-curator-cog", occupy=1)` block in each flow enforces this.

## Idempotency and run modes

- `backfill` — full-corpus rebuild. Wipes the derived layer (`concepts/`, `techniques/`, `instructors/`, `terminology/`) at start of run, preserving `_aliases.yaml` files. Bypasses the version-equality skip — every source re-ingests at the running curator version. Used when shipping curator behavior changes that should reshape the derived layer.
- `incremental` — steady-state, processes notes created since the last run. Honors the version-equality skip: sources whose existing `curator_version` matches the running version are no-ops. Auto-adds unknown instructor names to `instructors/_aliases.yaml` as identity mappings.

The curator version is auto-derived at runtime from `pyproject.toml` via `importlib.metadata.version("wiki-curator-cog")`. semantic-release bumps the version on push to main based on conventional commits (`feat:` → minor, `fix:` → patch). `WIKI_CURATOR_VERSION` env var is an escape hatch for local dev.

## Operating manual

The curator's per-page schema and routing rules live in `wcs-wiki/CLAUDE.md`. That file is the operating manual; this repo is the runtime. Schema changes happen in `CLAUDE.md`; the corresponding curator code change lands here as a `feat:` / `fix:` commit so semantic-release bumps the package version, which the curator picks up on its next deploy and uses to invalidate prior output via the equality-based idempotency check.
