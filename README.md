# wiki-curator-cog

[![CI](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/wiki-curator-cog?label=version)](https://github.com/mini-app-polis/wiki-curator-cog/releases)

Builds the WCS wiki from the canonical corpus in `api-kaianolevine-com`, and stores each page in the API as a document that the API serves filtered by what each reader may see.

It runs as the wiki-jobs Lambda worker (ADR-0006): when a lesson or note is composed, the API enqueues a `link` job, and each job names the ones that follow — `consolidate`, `generate`, then `materialise`, which stores the changed pages. The [`wcs-wiki`](https://github.com/mini-app-polis/wcs-wiki) markdown repo is a local export of the same pages, written by `render` and committed by hand if at all. The Railway `export` run that cloned, rendered and pushed it was retired with its service. The page schema is defined in `wcs-wiki/CLAUDE.md`.

See `docs/PIPELINE.md` for the ecosystem flow diagram and `docs/decisions/` for ADRs.

> **The self-generating wiki (ADR-0005, ADR-0006).** LLM passes link
> statements to the concepts they mention, fold narrow entities into broader
> ones, and maintain each entity's prose by integrating new statements into
> the page as it stands. Their state — and what it cost — is held by the API.
> They run locally (`link`, `consolidate`, `generate`, `job`) and as the
> wiki-jobs Lambda worker. The "deterministic" statements below describe the
> `render` step, which applies that state but calls no model.

---

## Architecture

```
Drive transcript drop
        │
        ▼
transcription-cog (LLM extraction) ──► api-kaianolevine-com (transcripts + sources)
                                            │  composed → link job
                                            ▼  this cog's FIFO queue
                                    the worker (Lambda)
                                      link → consolidate → generate → materialise
                                            │  state and page documents
                                            ▼
                                    api-kaianolevine-com ──► wcs.kaianolevine.com
                                            │
                                            ▼  `render`, locally
                                      wcs-wiki repo (markdown export)
```

The curator never accesses the database directly. All upstream data is fetched over HTTP from `api-kaianolevine-com`, authenticated with this cog's own named machine key.

Pipeline-evaluation findings (quality signals, judgment-call records) are emitted to `pipeline_evaluations` via `POST /v1/evaluations` — same pattern as every other cog.

---

## Running locally

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- The [Doppler CLI](https://docs.doppler.com/docs/install-cli), logged in (`doppler login`)
- A local clone of `wcs-wiki`
- `WIKI_CURATOR_COG_API_KEY` — this cog's own named key. The API grants it the
  `corpus-reader` and `pipeline-writer` roles; there is no fallback credential.

### Setup

Secrets come from Doppler's shared `dev` config through the
[Doppler CLI](https://docs.doppler.com/docs/install-cli) — nothing reads a
`.env` file. Local commands use `dev`. A command in this README that says
`doppler run --config prd` is a **production command**: it reads the
production corpus and state through the production API, and the ones that
send or import write to production, so run it on purpose, never by habit
(ecosystem-standards CFG-003).

```bash
brew install gnupg dopplerhq/cli/doppler   # once per machine
doppler login                              # once per machine

git clone git@github.com:mini-app-polis/wiki-curator-cog.git
cd wiki-curator-cog
doppler setup                              # reads doppler.yaml: mini-app-polis-ecosystem / dev
uv sync --all-extras
uv run pre-commit install
uv run pre-commit run --all-files
uv run check-doppler-keys                  # every required .env.example name is in dev
```

### Environment

Every variable this cog reads is listed, with its default and whether it is
required, in [`.env.example`](.env.example). On Lambda the worker gets them
from SSM Parameter Store, synced from Doppler `prd`; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Run tests

```bash
uv run pytest
```

`tests/unit` runs each pass and job against an in-process store.
`tests/integration` delivers real SQS-shaped messages to
`worker.lambda_handler` and drains the jobs they ask for — link, consolidate,
generate, materialise — with every API call going over HTTP through the shared
client to an in-memory API (respx) that validates each body against the
contract and refuses a field it would drop. No network, no model.

### Render locally (no commit, no push)

The proof-of-concept loop (ADR-0005). Writes into your existing clone and
stops — no commit, no push, no run report, and `log.md` is left alone — then
prints a JSON summary: pages written and removed, broken links, dropped
references, and how developed each entity is (thin, single-source, isolated).
Exits 1 if any link has no page, after writing, so the result can be read.

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run -- uv run python -m wiki_curator_cog.main render > "${TMPDIR:-/tmp}/render-summary.json"
```

To see the linking pass's references on the pages, overlay a links file
(from `link`, below). New concepts it proposes get pages, every entity page
gains `## Mentions` and `## Referenced by`, and per pair the newest statement
wins — a later denial removes a link:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main render > "${TMPDIR:-/tmp}/render-summary.json"
```

**Identity fixes.** If a note says two names are the same thing (an
`alias_of` relation), every local mode builds with them merged: the
preferred entity absorbs the other's content and lists its name as an alias,
and the other page goes. A newer `distinct_from` splits them again. Nothing
upstream changes; the render summary lists the merges under `merged`.

**Facets.** `consolidate` decides, for every concept and technique,
whether it is a facet of a broader one (*stationary anchor* → *anchor*), and
writes the decisions to `facets.json` (watermarked; already-decided entities
are skipped). Drills are asked the same about other drills; patterns only
whether they are another name for an existing pattern, since a variation is
its own figure. Nothing folds across kinds, and each kind has its own prompt. `render`, `generate` and `link` then fold each facet into its
broader page, listed under `## Details`. A facet's name and aliases stay on
the broader entity for matching, but the page's "also called" lists only
same-idea names, never a facet's. They pick up `facets.json` from next
in the state folder by default, or take `--facets PATH`. A note's
`facet_of` / `distinct_from` overrides the model; read `facets-review.md` to
see what was folded where. Each entity's support (sources taught in,
references from elsewhere, read from `links.json` beside the facets file or
the state folder) guides the call: thin entities fold readily, nothing folds into a
less-supported page, and thin entities with no home are listed at the end of
the review. `thin-review.md`, written beside the facets file, lists every thin
entity — what was said, which source, and where it ended up — for a private
scan; give one weight with a note. With the API as the store, the same list is
stored there for /admin/wiki. Every worker `consolidate` job, every
`link` job that links something, and "Rebuild every page" refresh it over the
whole corpus, with no model calls. A rerun with nothing new to decide makes no
model calls, so it is a free way to refresh both reviews.

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main consolidate --workers 16
```

### Cost: dry runs and usage

`link`, `generate` and `consolidate` cost money; `render` does not. Each
prints a `usage` block — calls, input and output tokens, and an estimated
`cost_usd` — read from the API's own token counts. Add `--dry-run` to see the
same block before spending: nothing is called or written and no API key is
needed, and the summary shows how much work is pending (`pending`, and
`pending_by_group` for `consolidate`). A dry run's calls and input tokens
come from the real prompts; its output tokens are a rough guess.

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main generate --dry-run
```

Dollars use a per-model default price; set `LLM_PRICE_INPUT_PER_MTOK` and
`LLM_PRICE_OUTPUT_PER_MTOK` to override. Version bumps are the expensive
moves: raising `LINKER_VERSION` or `GENERATOR_VERSION` replays the whole
corpus. `NOTE_RULE_VERSION` is the cheap one: raising it re-sends only the
authored notes' statements, so only the pages notes speak to are
regenerated. `consolidate` keeps one version per kind group
(`CONSOLIDATOR_VERSIONS`), so changing the drill prompt re-asks only drills.

### Where the state lives: the API (ADR-0006)

The three passes' state — `links.json`, `facets.json`, `prose.json` — is held
by the API. `render`, `link`, `generate` and `consolidate` read it from there
(`--state api`, the default) and write back the entries a run changed as it
goes, in batches, with a final flush. The files are kept as a cache in the
same layout, so the reviews, git history and Obsidian keep working, and they
are checked: if a file and the API disagree, the command stops before reading
or writing anything. That happens when a run's last write never arrived; run
`state-verify`, and if the file is the one to keep, `state-import`.
`--state files` uses the files alone, as before.

**One state folder.** Every command takes `links.json`, `facets.json` and
`prose.json` from one folder: `--state-dir`, or `$WIKI_REPO_PATH/.curator`
when it is not given. A file flag (`--links`, `--prose-file`, …) overrides
one file. A command that writes state and has no folder stops rather than
fall back to the current directory.

```bash
# read-only; exits 1 unless the API holds exactly what the files hold
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main state-verify
# send the files to the API (production under --config prd); safe to re-run
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main state-import
```

The import refuses a field the API does not store, goes up in chunks
(`--batch-size`, default 250), and is safe to repeat. A run's writes refuse
unknown fields the same way, and a refused write stops the run.

### One job per message (ADR-0006)

`units.handle` is the body of the wiki-jobs worker: one job in, its
result and the jobs that should follow out. `link` takes one source;
`consolidate`, `generate` and `materialise` take named entities. Each is a
no-op when its watermark says there is nothing new, so a retried or
duplicated message costs nothing. Every follow-on carries `origin_source_id`,
the source whose `link` started the chain (none for admin or operator work),
and `materialise` stores it on each page write with the run that wrote it: the
queue message id, or `local`. The `job` command runs one locally, against
production and the state store — the way to see what one new note costs:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main job \
  --job '{"job": "link", "source_id": "<source uuid>"}' --dry-run
```

The result's `next` lists the follow-on jobs (`consolidate` for the source's
undecided entities, `generate` — in chunks of entities — for every page its
newly linked statements touched, a repeated job touching none; `materialise`
for every page a `generate` was asked about). The command does not run them;
run each the same way, or let the worker's dispatch do it.

### Page documents (ADR-0006)

`materialise` builds each entity page as a structured document — definition,
In practice, Details, statements by teacher with their citations, Mentions,
Referenced by — and stores it in the API (`POST /v1/wcs/wiki/pages`), which
serves it filtered to the sources each reader can see. It calls no model.
Folded names lose their pages when the page they folded into is written.
Links in the text are neutral (`[[slug|label]]`, `[[source:<uuid>|label]]`),
for each reader to turn into its own URLs. `render` writes the repo's markdown
from the same documents (`pages.page_markdown`), so the export and the API
show one page model.

Every page at once — the backfill, and the way to rebuild the documents after
the page logic changes; `--entity` for named pages, `--dry-run` to build
without storing:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main materialise
```

A local `link`, `consolidate` or `generate` against the API ends the way its
worker job does: it rebuilds the pages it changed (`pages_rebuilt` in the
summary) — the generated or condensed pages, or every page after a link or
consolidate run that changed anything. A dry run, or `--state files`, stores
no pages.

### The worker (ADR-0006)

On AWS the same units run as a Lambda behind this cog's FIFO queue
(`wiki_curator_cog.worker.lambda_handler`, deployed by CI on each release;
the function, queue and alarms are declared in mini-app-polis/infra). The API is the only producer: it enqueues a `link`
when a source is composed — only while `flags.wcs.wiki_jobs_enabled` is on,
seeded off — and the follow-on jobs each job names, which the worker posts to
`POST /v1/wcs/wiki/jobs`. Jobs run side by side only where they write different state entries (below); each reads the export and the
state from the API into a fresh temporary folder, so a warm container never
holds stale state. The export is the expensive read. The API builds it once per
corpus revision, and the shared client sends back the ETag of the copy a warm
container kept, so an unchanged corpus comes back as a 304. Don't add
concurrency that would make jobs read the export in parallel without checking
that it still costs one build per revision (the 2026-10-18 incident). A failed job is returned to the queue and retried (its
finished parts are no-ops), and an unreadable message is reported once and
dead-lettered.

### Link locally (ADR-0005)

The linking pass, local half: reads the export, asks an LLM which concepts
each statement mentions, and writes the references to the links state (the
API, cached in `links.json`). It is also the watermark: a statement already
linked under the current linker version is skipped on the next run. A
readable `links-review.md` is written next to it for the statements that run
covered.

```bash
# a reproducible sample of 20, spread across statement kinds
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main link --sample 20
```

Needs `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=openai` and `OPENAI_API_KEY`);
`LLM_MODEL` overrides the model.

### Generate overviews locally (ADR-0005)

Generation, local half: for each entity, an LLM is given the page's current
definition and context and the statements not yet integrated — the entity's
own, plus the statements that refer to it from `links.json` — and returns an
updated **definition** (the page's first section: one or two plain sentences)
and optional **context** (a short "In practice" section below it), newest
statements winning. Results go to the prose state (the API, cached in
`prose.json`; also the watermark: an entity with nothing new costs no call),
with a readable `prose-review.md` beside it.

```bash
# named entities, or --sample N for a reproducible spread across kinds
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main generate --entity connection --entity anchor-step

# then put the overviews on the pages (with the link overlay)
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main render > "${TMPDIR:-/tmp}/render-summary.json"
```

`--workers N` runs N model calls at once (default 4 for `generate`, 1 for
`link`); the files are still written one entity or batch at a time. The
limit is the account's rate limit — rate-limited calls are retried.

The page is the base: if an overview was edited on the page, the next run
builds on the edit rather than replacing it. Editing is optional —
corrections normally arrive as notes. A note outranks the page: the model
sees which statements come from notes, and where a note says what an idea
is, the definition follows it.

**The shape, and rewriting to it.** A definition is one or two
sentences; "In practice" is at most three sentences of one idea each, none
over 30 words, with no long dashes. `generate --condense` rewrites only the
pages over that shape, with no new evidence (`--entity` to try a few first).
It keeps a definition that is within its shape, retries once with what was
wrong, and stores nothing that still breaks the shape (`not_kept_why` says
why). `prose-review.md` lists each rewrite with the text it replaced.

A rewrite can change meaning: the full run on 2026-10-19 did on about one
page in nine. Read `prose-review.md`, or have it reviewed, before trusting a
mass rewrite. `generate --revert --entity SLUG` puts a page back to its text
before its last rewrite, with no model call. It refuses a page whose last
change took in new evidence, and records the text as chosen by a person
(`page`), which later rewrite runs leave alone.

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main generate --condense --dry-run
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main generate --revert --entity tuck-turn
```

---

## Observability

Three layers:

1. **Structured logs** — `mini_app_polis` logger throughout, JSON output via `common-python-utils`; one timing line per job.
2. **Sentry** — the exception that fails a job (the shared cogs project, tagged `service:wiki-curator-cog`).
3. **Run report** — one per job (`wiki-link`, `wiki-generate`, …), as every cog sends one per run. It names what changed: references a link found, folds a consolidation moved, pages whose prose was generated, and pages a materialise created, changed or removed. A page rewritten identically only counts toward `pages_unchanged`. A job that refreshes the thin list (`consolidate`, `link`, the every-page rebuild) also counts it (`thin_entities`, `thin_written`, `thin_removed`). It also gives model calls and cost as counts, posted whenever the job did something (pages written or removed, statements linked, decisions or prose made, a model call, a follow-on queued) and a log line only when it found nothing to do, a WARN when it finished with paid-for work left undone (a failed link or consolidation batch, an entity whose prose failed), and sent on the way out when a job raises. An unreadable message, which never reaches a job, is reported once by the worker.
4. **The dead-letter queue** — a job that keeps failing lands there, and its alarm (declared in mini-app-polis/infra) says so.

---

## Key concepts

**The curator does not invent content.** Every wiki claim traces to an upstream source. The schema in `wcs-wiki/CLAUDE.md` is the operating manual; this cog implements it.

**A deterministic render over model-written state.** `render` and `materialise` call no model: given the same export and the same state (references, facets, prose), they produce the same pages. The model calls are in `link`, `consolidate` and `generate` — locally or in the worker — each watermarked, so a re-run pays only for what is new.

**Idempotency.** Every model pass is watermarked in the API, so a repeated job makes no model call, and `materialise` rewrites the same document. The render is a pure function of the export and the state: an unchanged corpus yields an unchanged tree.

**Curator version.** Auto-derived at runtime from the package's `pyproject.toml`. semantic-release bumps the version on push to main based on conventional commits: `feat:` → minor, `fix:` → patch, `BREAKING CHANGE:` → major. `chore:` / `docs:` / `style:` commits do not produce a release.

**Side by side only where it is safe.** The queue is FIFO and jobs that write the same state entries never run side by side. `consolidate` and `generate` share one message group, so two never pay for one entity; `link` runs a group per source and `materialise` spreads over a few page groups, so lessons link and pages rebuild side by side, up to the function's concurrency ceiling (the groups are set by api-kaianolevine-com when it enqueues; the ceiling is declared in mini-app-polis/infra).

**Read-only against the corpus.** The curator reads note content with `GET` only. It writes its own state (`/v1/wcs/wiki/state/*`) and page documents (`/v1/wcs/wiki/pages`), and asks for follow-on jobs (`/v1/wcs/wiki/jobs`).
