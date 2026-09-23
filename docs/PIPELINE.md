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
           │ GET /v1/wcs/notes/all (read-only, named machine key)
           ▼
┌──────────────────────────┐
│   wiki-curator-cog        │  (this repo)
│   one mode: export        │  (deterministic, no LLM)
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
- **Layer 2 — LLM polish pass.** Operates on Layer 1's output and refines it: prose synthesis where Layer 1 emits a mechanical Overview template, instructor-page `## Background` / `## Teaching themes` / `## Notable framings`, terminology-page creation when vocabulary collapses are ambiguous, lint findings, query answering. Runs after Layer 1 finishes. Its coordination is still to be designed; the Prefect slot this originally named is gone (ADR-004).

This cog implements Layer 1.

## Auth

- Curator authenticates to `api-kaianolevine-com` with its own named machine key, `WIKI_CURATOR_COG_API_KEY`, held in Doppler. The key is the identity claim — the API matches it against the machines it declares and learns the caller's name from the match. No Clerk, no token exchange, no fallback: unset or wrong is a 401 on every call.
- Roles `corpus-reader` and `pipeline-writer`. `corpus-reader` carries `wcs.corpus.read`, which is what lets the curator read all notes regardless of `is_default_visible` or `visibility`; `pipeline-writer` covers posting findings.
- GitHub fine-grained PAT scoped to `wcs-wiki` only; passed as `GH_TOKEN` in env. Used to clone and push.
- No database credentials. The curator never touches the database directly.

## Observability

| Layer | Signal              | Where                                             |
| ----- | ------------------- | ------------------------------------------------- |
| L1    | Liveness            | Healthchecks.io: `/start`, then success or `/fail`, per run |
| L2    | Structured logs     | `mini_app_polis` JSON logger                      |
| L3    | Unhandled exceptions| Sentry                                            |
| L4    | Quality signals     | `pipeline_evaluations` via `POST /v1/evaluations` |

## Concurrency

One wake is one run in one process, which clones into its own container's `/tmp` and shares nothing — so the working-tree race the old Prefect concurrency slot guarded against cannot happen. Railway will not start the service again while a previous start is `Active`. What two overlapping runs could still race on is the push, and git arbitrates that: the loser is rejected non-fast-forward and fails visibly. That is weaker than a lock and strong enough for a manually-triggered rebuild; it would not be enough for anything automatic and frequent. See ADR-004.

## One mode, and the run budget

There is one mode, `export`: a full, deterministic re-render of the whole
corpus from the canonical entity graph. It takes no arguments, and running it
twice against unchanged upstream data produces the same bundle — which is why
the trigger can be crude and why redundant runs cost time rather than
correctness. The `backfill` / `incremental` / `interactive` split described in
ADR-001 no longer exists.

A run has a declared budget, `RUN_TIMEOUT_SECONDS` (default 1800). This is not
a Lambda-style ceiling being imitated for its own sake: Railway silently skips
every later start while a previous one is still `Active`, so a run that hangs
takes all its successors with it and says nothing. `_deadline.py` raises inside
the run a margin before the budget, so the failure travels the ordinary path —
the report is sent, `main` exits non-zero, and the next start is free to happen.

The curator version is auto-derived at runtime from `pyproject.toml` via
`importlib.metadata.version("wiki-curator-cog")`. semantic-release bumps it on
push to main based on conventional commits (`feat:` → minor, `fix:` → patch).

## Operating manual

The curator's per-page schema and routing rules live in `wcs-wiki/CLAUDE.md`. That file is the operating manual; this repo is the runtime. Schema changes happen in `CLAUDE.md`; the corresponding curator code change lands here as a `feat:` / `fix:` commit so semantic-release bumps the package version, which the curator picks up on its next deploy and uses to invalidate prior output via the equality-based idempotency check.
