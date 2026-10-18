# ADR-006 — The wiki in the pipeline: per-unit Lambda jobs, page documents in the API, served dynamically

**Date:** 2026-10-16
**Status:** Accepted — steps 1–4 built 2026-10-17: state in the API and imported (MINI-89), the API as the curator's store and one unit per job (MINI-94), the wiki-jobs queue and Lambda worker with API dispatch, flag on (MINI-95), page documents and visibility (MINI-96). The Railway service and its `export` code are retired (MINI-95); the site's /wiki pages (MINI-97) are open; the wcs-wiki repo stays as a local export from the page documents (MINI-99, decided 2026-10-17)
**Supersedes (when built):** ADR-004 (one-shot on Railway); ADR-005's local files in `wcs-wiki/.curator/` as the store
**Related:** ADR-005; `evaluator.yaml` CD-021 (Lambda); `api-kaianolevine-com` (canonical store, scope-guarded `/v1/wcs/*`); `website-astro-wcs` (static shells, data at request time); Asana MINI-89, MINI-94 – MINI-99

## Context

ADR-005 proved the self-generating wiki on a laptop: `link`, `consolidate`,
`generate` and `render` run against the production export, keep their state in
JSON files in the private `wcs-wiki` repo, and write markdown that is read in
Obsidian. That proves the mechanism, not where it lives. Three things are
missing:

- **A trigger.** Nothing runs when a lesson or note is ingested; Kaiano runs
  the four commands by hand.
- **A durable home.** The state (references, facet decisions, prose and its
  revisions) is in files on one machine and in git, not in the API that owns
  the rest of the WCS data.
- **An official way to read it.** The product is a folder of markdown in a
  private repo. Kaiano does not want Obsidian to be the reader.

The rest of the fleet has already settled how work like this runs: watcher (a
scheduled Lambda) → `api-kaianolevine-com` → an SQS queue → a Lambda worker,
with the API owning the data (evaluator, deejay and transcription are all on
it). On 2026-10-14 the curator was told it would join them once its design was
done; this ADR is that design.

## Decision

### Jobs: one invocation per unit of work, dispatched by the API

When transcription has composed a source, the API enqueues wiki work on a
`wiki-jobs` queue. The curator worker handles four job types, each a pure
function of its input and the API's current state:

| Job | Unit | Writes | Then the API enqueues |
|---|---|---|---|
| `link` | one source's statements | references (with polarity) | `consolidate` for new entities; `generate` for every entity referenced or taught |
| `consolidate` | a batch of undecided entities | facet decisions | `generate` for each parent that gained a facet |
| `generate` | one entity | a prose revision | `materialise` for that page |
| `materialise` | one page | the page document | — |

- **The watermarks become columns.** `LINKER_VERSION`, `CONSOLIDATOR_VERSION`
  and `GENERATOR_VERSION` and the per-statement / per-entity integration sets
  move into the API, so a retried or duplicated message is a no-op and raising a
  version is a replay, as it is locally today.
- **Notes need nothing new.** `alias_of`, `facet_of` and `distinct_from` still
  arrive as relations through transcription; they change the folds the next
  `materialise` applies.
- **The local CLI stays.** `link`, `consolidate`, `generate` and `render` call
  the same per-unit functions, so prompt work still iterates on the laptop
  against production, read-only.
- **Infra is the fleet's.** A `cog-worker` module entry in `mini-app-polis/infra`,
  a FIFO queue with a DLQ, the shared `lambda-deploy.yml`, secrets from SSM —
  copied from transcription-cog. The Railway service and its git-push render
  retire when this lands.

### Storage: the API holds the state and the pages (MINI-89)

New tables in `api-kaianolevine-com`, the durable half of ADR-005:

- **references** — statement, target entity, polarity, linker version, model.
- **facet decisions** — entity, parent, same-as, reason, decided by (model or
  note), version.
- **prose revisions** — entity, definition, In practice, integrated statement
  ids, generator version, model; the latest is current, the rest is history.
- **page documents** — one JSON document per page, written by `materialise`.

A one-time import loads today's `links.json`, `facets.json` and `prose.json`,
so the work already paid for carries over.

**As built (2026-10-17).** The state tables hold the files field for field,
keyed as the files are — statement keys and entity slugs, no foreign keys
into the substrate — so the import is lossless and every watermark holds:
`wcs_wiki_statements` / `wcs_wiki_references`, `wcs_wiki_facets`,
`wcs_wiki_prose` / `wcs_wiki_prose_revisions`, and a header row per document.
Concepts the linker proposed stay in these tables by slug; they get no
entity row, so the notes UI never sees them. `GET` / `POST
/v1/wcs/wiki/state/{links,facets,prose}` read whole documents and upsert
entries, under `wcs.corpus.read` and a new `wcs.wiki.write` (role
`wiki-writer`, held by the curator). The curator's `state-import` sends the
files in chunks and `state-verify` compares them, entry by entry, with what
the API returns; on the real files the round trip was exact. Page documents
wait for their schema (MINI-96).

**MINI-94, part 1 (2026-10-17): the API is the store.** The local commands
read each state document from the API and write back changed entries in
batches during a run, with a final flush; the files remain as a cache in
their old layout (byte-identical on the real state), checked against the API
on every read. A disagreement stops the command before it reads or writes —
the case of a run whose last write never arrived — so neither side
overwrites the other. `--state files` keeps the file-only behaviour. The
per-unit functions for the worker are part 2.

**MINI-94, part 2 (2026-10-17): one unit per job.** `units.handle(job, ctx)`
runs `link` (one source's statements), `consolidate` or `generate` (named
entities) against a `RunContext` built once per invocation —
the export with its folds applied, the state store, the model — and returns
the follow-on jobs for the API to enqueue, rather than enqueuing them
itself: `link` names `consolidate` for the source's undecided entities and
`generate` for every entity its newly linked statements taught or referred
to, so a repeated job asks for nothing; `consolidate` names `generate` for
each page that gained a fold. Follow-on generation goes in chunks of
entities, one job each, rather than a message per entity: every invocation
reads the export and the state, and a no-op still pays for that read. The table above is what the
dispatch implements. Each unit is a no-op under its watermark. `materialise`
joins with the page documents (MINI-96). The units read and write whole
state documents for now; per-entry reads are an API change to make if the
worker's reads prove slow.

**MINI-95 (2026-10-17): the queue and the worker.** `wiki-curator-jobs.fifo`,
one message group, reserved concurrency 1: jobs never run side by side, so
two cannot pay for one entity or race on a document. Messages are
`{"type": "wiki.job", "version": 1, "payload": <job>}`. The API enqueues a
`link` after composing a source — behind `flags.wcs.wiki_jobs_enabled`,
seeded off (migration 055), because every job costs model calls — and
`POST /v1/wcs/wiki/jobs` (scope `wcs.wiki.write`) enqueues the follow-ons
the worker posts back, keeping the API the only producer. The worker
(`worker.lambda_handler`) reads state into a fresh temporary folder per
invocation, sends a job's follow-ons before it counts as done, and returns
failed records to the queue. The Railway service and its git-push render
were retired on 2026-10-17, once page documents existed (MINI-96) and the
markdown repo became a local export only (MINI-99): the service by hand, and
`.railway/`, `railpack.json`, the `export` mode, `boot.py`, `git_ops.py`,
`config.py` and GitPython from this repo.

**MINI-96 (2026-10-17): page documents.** `materialise` (`units.materialise_pages`)
builds each named page with `pages.page_document` and posts it to
`POST /v1/wcs/wiki/pages` (scope `wcs.wiki.write`), in chunks, removing in the
same pass every name folded into a page it writes and any slug that is no
longer a page. `generate` names `materialise` for every page it was asked
about, model call or not, since a new reference or fold changes a page without
new prose; `materialise` names nothing. The schema is `WcsWikiPage` in
common-python-utils. Two refinements to the design below, both found while
building it:

- **Visibility is read live, not trusted from the document.** Each citation
  carries its source id and `public` as it was when the page was built, but
  the API rewrites `public` from `wcs_sources` on every read and filters on
  it. A stored flag goes stale the moment a source is made private in
  /admin/notes; a stale "public" is a leak.
- **Plain and admin routes, as for sources**, rather than one route that
  inspects scopes. `GET /v1/wcs/wiki/pages[/{slug}]` (`wcs.notes.read`)
  serves the evidence the caller can see — default-visible sources and their
  grants, the rule /notes uses — and drops a hidden source's links from the
  text to their label. A page whose `evidence_sources` (its own statements'
  and those referring to it) are all hidden is a 404 for that reader.
  `GET /v1/wcs/wiki/admin/pages[/{slug}]` (`wcs.corpus.read`) serves
  everything; `?as=public` shows what a reader with no grants would see.

What the filter cannot do: generated prose may draw on private sources it no
longer cites. The public-only rebuild of ADR-005 is still what closes that,
before the wiki has any reader but Kaiano.

The markdown export reads the same documents: `render` builds them and writes
each entity page with `pages.page_markdown`, byte for byte the page it wrote
before. Source, instructor and view pages, and the index, stay markdown-only.

### Page logic stays in the curator

Folding (aliases, facets, nested Details), the reference overlay, Mentions /
Referenced by, link repair and By teacher are curator code today and stay
curator code: `materialise` runs them for one page and stores the result. The
API does not recompute pages; it stores, filters and serves them. One
implementation, in one place, testable without the API.

A page document is structured, not rendered markup: name, kind, definition,
In practice, Details (name, gist, depth), By teacher (statements with their
citations), Mentions, Referenced by (links, or a count above 25), aliases.
Text fields are markdown; links between pages are a neutral `[[slug|label]]`
form that the reader rewrites to its own URLs.

### Visibility: one document, citations tagged, the API filters

Each citation in a page document carries its source's visibility. The API
returns everything to the admin scope and strips private citations for any
other reader. So there is one stored document, not a public and a private
copy, and the admin can ask for the public view of the same page to compare
(`?as=public`). For now Kaiano is the only reader, as admin; the public view
exists so that opening the wiki up later is a scope change, not a rebuild.

### Reading: served dynamically, no static build

The wiki is read on `wcs.kaianolevine.com`, in the pattern the site already
uses for notes and sets: a static shell page, with the content fetched at
request time from the scope-guarded API (Clerk token in the browser). No
`getStaticPaths`, no rebuild per lesson, no build that depends on the API.

- `/wiki` — index: search, kinds, recently changed pages.
- `/wiki/page?slug=<slug>` — one page, rendered from its document.
- `/admin/wiki` — what the review files are today: facet decisions by page,
  thin entities with their quotes and sources, and "this source changed these
  pages".

A page edit, when it comes, is written to the API as a prose revision marked
as an edit (the page-as-base rule of ADR-005 carries over).

### Later, not now

- **Add a note from a page.** A form on a wiki page that creates a note source
  through the API, skipping the Drive round-trip, and flows through the same
  jobs. Wanted, not urgent (MINI-98).
- **The `wcs-wiki` repo.** Decided 2026-10-17, ahead of `/wiki` because it
  shaped MINI-96 (MINI-99): it stays as a local markdown export, written by
  `render` from the same page documents the API stores. The Railway `export`
  mode and its git push retire; the repo is a local reading and diffing copy,
  committed by hand if at all. Whether to delete it is revisited once `/wiki`
  works.
- **Public, indexable pages.** Client-rendered pages are fine for one signed-in
  reader. If the wiki goes public and search indexing matters, server
  rendering at the edge (a Worker in front of the shell) is the option to
  weigh then — not a reason to add an adapter now.

## Plan

1. **MINI-89** — storage in the API: tables, import of the `.curator/` files,
   read endpoints with the visibility filter.
2. **MINI-94** — the curator as per-unit functions (`link_source`,
   `consolidate_batch`, `generate_entity`, `materialise_page`) reading and
   writing through the API; the local CLI on top of them.
3. **MINI-95** — `wiki-jobs` queue, Lambda worker and infra; API dispatch
   after a source is composed; the Railway service retired.
4. **MINI-96** — page documents: `materialise`, the document schema, the
   admin `?as=public` view.
5. **MINI-97** — `/wiki`, `/wiki/page` and `/admin/wiki` on
   `wcs.kaianolevine.com`.
6. **MINI-98** — add a note from a page (later).
7. **MINI-99** — decide the `wcs-wiki` repo's future (after step 5).

Steps 1–3 do not wait on the remaining prompt refinement: a prompt change is a
version bump and a replay.

## Consequences

- The curator becomes a worker like the other cogs; ADR-004's one-shot,
  wake-and-run shape and the git push go away.
- A lesson changes the wiki minutes after it is ingested, with no command run
  by hand; the cost per lesson is ADR-005's estimate (~$0.20–0.40).
- The API grows four tables and a page-serving path, and becomes the place
  where visibility is enforced for the wiki as for everything else.
- The site gains wiki pages without a build step or an adapter, consistent with
  its static-shell decision.
- Obsidian stops being the reader once `/wiki` works.

## Alternatives considered

- **The API computes pages on read.** Keeps no stored pages, but duplicates the
  folding and overlay logic in a second codebase. Rejected: one implementation.
- **A static build per change** (getStaticPaths plus a deploy hook). Rejected
  by Kaiano, and by the site's own history: builds that depend on the API and
  page counts that grow with the corpus are what the sets and notes pages moved
  away from.
- **Two stored documents, public and private.** Simpler reads, but two copies
  to keep in step. Rejected for one document with tagged citations, filtered
  by scope.
- **Server rendering with an Astro adapter.** The site removed its adapter to
  free its Astro version; nothing here needs it back.
