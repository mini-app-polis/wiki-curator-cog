# Pipeline architecture

`wiki-curator-cog` builds the WCS wiki from the canonical entity graph in `api-kaianolevine-com`. The structured data comes from `transcription-cog`'s LLM extraction over lesson and note transcripts; this cog adds the wiki's own LLM work — references between entities, facet folding, page prose (ADR-0005) — keeps its state in the API (ADR-0006), and stores each finished page there as a document. The API serves the documents to `wcs.kaianolevine.com`, filtered by what each reader may see. The `wcs-wiki` markdown repo is a local export of the same pages.

## Flow diagram

```
Drive transcript drop
        │
        ▼
┌──────────────────────────┐
│   transcription-cog       │
│   LLM extraction          │
└──────────┬────────────────┘
           │ POST /v1/wcs/transcripts, POST /v1/wcs/sources
           ▼
┌──────────────────────────────────────────────┐
│   api-kaianolevine-com                       │
│   composition → canonical entity graph       │
│   a composed source enqueues a `link` job    │
└──────────┬───────────────────────────────────┘
           │ this cog's FIFO queue (groups by what a job writes)
           ▼
┌──────────────────────────────────────────────┐
│   the worker (Lambda, this repo)             │
│   link → consolidate → generate → materialise│
│   reads GET /v1/wcs/wiki/export and state;   │
│   writes state, page documents, follow-ons   │
└──────────┬───────────────────────────────────┘
           │ POST /v1/wcs/wiki/{state/*, pages, jobs}
           ▼
┌──────────────────────────────────────────────┐
│   api-kaianolevine-com                       │
│   GET /v1/wcs/wiki/pages[/{slug}] (filtered) │
│   GET /v1/wcs/wiki/admin/pages[/{slug}]      │
└──────────┬───────────────────────────────────┘
           ▼
   wcs.kaianolevine.com /wiki

Locally: `render` writes the same pages as markdown into a wcs-wiki clone.
```

## Jobs

| Job | Unit | Writes | Follows with |
|---|---|---|---|
| `link` | one source's statements | references; the thin list, when it linked something | `consolidate` for its undecided entities; `generate` for every page its newly linked statements touched |
| `consolidate` | named entities | facet decisions; the thin list, over the whole corpus | `generate` for each page that gained a fold |
| `generate` | named entities | prose revisions | `materialise` for every page it was asked about |
| `materialise` | named pages, or none for every page (the /admin/wiki rebuild, one job) | page documents; removes pages folded away; every page also refreshes the thin list | — |

Every follow-on carries `origin_source_id`: a `link` job names its own source, every other job passes on the one it was given, and admin or operator work has none. `materialise` stores it on each page write, with the queue message id as `origin_job_id`.

Every model pass is watermarked in the API, so a repeated job makes no model call. `materialise` calls no model. The same functions run locally through the `job`, `link`, `consolidate`, `generate` and `materialise` commands (README).

## Auth

- The curator authenticates to `api-kaianolevine-com` with its own named machine key, `WIKI_CURATOR_COG_API_KEY` — in Doppler for local commands, in SSM for the worker. The key is the identity claim; there is no fallback, so unset or wrong is a 401 on every call.
- `corpus-reader` carries `wcs.corpus.read`, which lets the curator read the whole corpus regardless of per-source visibility; `wiki-writer` carries `wcs.wiki.write`, for its state, page documents and follow-on jobs.
- No database credentials. The curator never touches the database directly.

## Observability

| Layer | Signal | Where |
| ----- | ------ | ----- |
| L1 | Structured logs, one timing line per job | `mini_app_polis` JSON logger |
| L2 | The exception that fails a job | Sentry (shared cogs project, `service:wiki-curator-cog`) |
| L3 | Each job's outcome: counts, cost, work left undone (WARN), a raise | one run report per job (`run_report`), as every cog sends |
| L4 | A job that keeps failing | the dead-letter queue and its alarm (declared in mini-app-polis/infra) |

## Concurrency and the job budget

The queue is FIFO, and jobs that write the same state entries never run side by side. `consolidate` and `generate` share one message group, so two never pay for one entity; `link` runs a group per source and `materialise` spreads over a few page groups, so lessons link and pages rebuild side by side, up to the function's concurrency ceiling (the groups are set by api-kaianolevine-com when it enqueues; the ceiling is declared in mini-app-polis/infra). A job's budget is the invocation's remaining time; `mini_app_polis.lambda_worker.deadline` stops it a margin before Lambda would, so it fails cleanly and its record returns to the queue.

The curator version is auto-derived at runtime from `pyproject.toml` via `importlib.metadata.version("wiki-curator-cog")`. semantic-release bumps it on push to main based on conventional commits (`feat:` → minor, `fix:` → patch), and CI deploys each release to the Lambda.

## Operating manual

The page schema lives in `wcs-wiki/CLAUDE.md`; this repo is the runtime. The page logic — folds, the reference overlay, By teacher — is `pages.py` and the modules it builds on; changing it means re-running `materialise` for every page, which the `rebuild` job in CI does after each deploy.
