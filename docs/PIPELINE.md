# Pipeline architecture

`wiki-curator-cog` sits one step downstream of `transcription-cog`. It consumes structured WCS notes from `api-kaianolevine-com` (HTTP API, read-only) and produces a markdown knowledge base in the `wcs-wiki` repo.

## Flow diagram

```
Drive transcript drop
        │
        ▼
┌──────────────────────────┐
│   transcription-cog       │  (existing)
│   wcs-transcripts mode    │
└──────────┬────────────────┘
           │ POST /v1/wcs/transcripts
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
│   backfill | incremental  │
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

## Auth

- Curator authenticates to `api-kaianolevine-com` via Clerk M2M JWT (Project Keystone). Machine secret in Doppler.
- `wcs_admin` scope required (curator reads all notes regardless of visibility).
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

## Operating manual

The curator's per-source behavior is governed by `wcs-wiki/CLAUDE.md`. That file is the operating manual; this repo is the runtime. Schema changes happen in `CLAUDE.md`, paired with a bump to `WIKI_CURATOR_VERSION` when behavior changes in a way that affects existing output.
