# wiki-curator-cog

[![CI](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/wiki-curator-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/wiki-curator-cog?label=version)](https://github.com/mini-app-polis/wiki-curator-cog/releases)

Renders the [`wcs-wiki`](https://github.com/mini-app-polis/wcs-wiki) markdown knowledge base from the canonical WCS corpus in `api-kaianolevine-com`.

Reads the canonical WCS entity graph from `api-kaianolevine-com` in one call (`GET /v1/wcs/wiki/export`, read-only), deterministically renders it onto source / concept / technique / instructor / terminology pages and views in a clone of `wcs-wiki`, and pushes to GitHub. The curator does not call any LLM at runtime — upstream `transcription-cog` has already extracted the structured data from raw transcripts. The schema this cog implements is defined in `wcs-wiki/CLAUDE.md`; the strategic intent and queued work live in `wcs-wiki/ROADMAP.md`.

**One mode, `export`.** A run takes no arguments: it fetches the whole corpus,
re-renders every page from it, removes derived pages that are no longer
produced, commits what changed and pushes. Running it twice over unchanged
upstream data produces the same bundle and the second run commits nothing —
which is why the trigger is allowed to be crude. The three-mode split this
README used to document was retired by ADR-002 and ADR-004.

Triggered manually. One wake is one run: the process renders once and exits. The direction of travel is a run per source change, asked for by `api-kaianolevine-com`; nothing is built for that yet (ADR-004).

See `docs/PIPELINE.md` for the ecosystem flow diagram and `docs/decisions/` for ADRs.

> **Direction (ADR-005, planned):** the cog is becoming a self-generating wiki.
> An LLM will maintain each entity's prose by integrating new statements —
> lessons and correction notes alike, newer winning — into the current page,
> human edits included, with the prose and its revisions owned by the API.
> Until that code lands, the "no LLM at runtime" and "deterministic" statements
> below describe the current renderer, not the plan. Tracked as MINI-29.

---

## Architecture

```
Drive transcript drop
        │
        ▼
transcription-cog (LLM extraction) ──► api-kaianolevine-com (transcripts + sources)
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
- The [Doppler CLI](https://docs.doppler.com/docs/install-cli), logged in (`doppler login`)
- A local clone of `wcs-wiki`
- `WIKI_CURATOR_COG_API_KEY` — this cog's own named key. The API grants it the
  `corpus-reader` and `pipeline-writer` roles; there is no fallback credential.

### Setup

Secrets come from Doppler's shared `dev` config through the
[Doppler CLI](https://docs.doppler.com/docs/install-cli) — nothing reads a
`.env` file, and local runs never use `prd`.

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

### Run tests

```bash
uv run pytest
```

### Render locally (no commit, no push)

The proof-of-concept loop (ADR-005). Writes into your existing clone and
stops — no commit, no push, no run report, and `log.md` is left alone — then
prints a JSON summary: pages written and removed, broken links, dropped
references, and how developed each entity is (thin, single-source, isolated).
Exits 1 if any link has no page, after writing, so the result can be read.

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run -- uv run python -m wiki_curator_cog.main render > render-summary.json
```

To see the linking pass's references on the pages, overlay a links file
(from `link`, below). New concepts it proposes get pages, every entity page
gains `## Mentions` and `## Referenced by`, and per pair the newest statement
wins — a later denial removes a link:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main render > render-summary.json
```

**Identity fixes.** If a note says two names are the same thing (an
`alias_of` relation), every local mode builds with them merged: the
preferred entity absorbs the other's content and lists its name as an alias,
and the other page goes. A newer `distinct_from` splits them again. Nothing
upstream changes; `render-summary.json` lists the merges under `merged`.

**Facets (MINI-93).** `consolidate` decides, for every concept and technique,
whether it is a facet of a broader one (*stationary anchor* → *anchor*), and
writes the decisions to `facets.json` (watermarked; already-decided entities
are skipped). Drills are asked the same about other drills; patterns only
whether they are another name for an existing pattern, since a variation is
its own figure. Nothing folds across kinds, and each kind has its own prompt. `render`, `generate` and `link` then fold each facet into its
broader page, listed under `## Details`. They pick up `facets.json` from next
in the state folder by default, or take `--facets PATH`. A note's
`facet_of` / `distinct_from` overrides the model; read `facets-review.md` to
see what was folded where. Each entity's support (sources taught in,
references from elsewhere, read from `links.json` beside the facets file or
the state folder) guides the call: thin entities fold readily, nothing folds into a
less-supported page, and thin entities with no home are listed at the end of
the review. `thin-review.md`, written beside the facets file, lists every thin
entity — what was said, which source, and where it ended up — for a private
scan; give one weight with a note. A rerun with nothing new to decide makes no
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
corpus. `consolidate` keeps one version per kind group
(`CONSOLIDATOR_VERSIONS`), so changing the drill prompt re-asks only drills.

### Where the state lives: the API (ADR-006, MINI-89, MINI-94)

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

### One job at a time (ADR-006, MINI-94)

`units.handle` is the body of the wiki-jobs worker (MINI-95): one job in, its
result and the jobs that should follow out. `link` takes one source;
`consolidate` and `generate` take named entities. Each is a
no-op when its watermark says there is nothing new, so a retried or
duplicated message costs nothing. The `job` command runs one locally, against
production and the state store — the way to see what one new note costs:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main job \
  --job '{"job": "link", "source_id": "<source uuid>"}' --dry-run
```

The result's `next` lists the follow-on jobs (`consolidate` for the source's
undecided entities, `generate` — in chunks of entities — for every page its
newly linked statements touched; a repeated job touches none). The command does
not run them; run each the same way, or let the worker's dispatch do it.

### Link locally (ADR-005, MINI-84)

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

### Generate overviews locally (ADR-005, MINI-86)

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
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main render > render-summary.json
```

`--workers N` runs N model calls at once (default 4 for `generate`, 1 for
`link`); the files are still written one entity or batch at a time. The
limit is the account's rate limit — rate-limited calls are retried.

The page is the base: if an overview was edited on the page, the next run
builds on the edit rather than replacing it. Editing is optional —
corrections normally arrive as notes.

### Run an export locally

`WIKI_REPO_PATH` is not in Doppler; point it at your wcs-wiki clone in the
shell. `doppler run` passes it through because Doppler holds no value for
that name; if it did, Doppler's would win:

```bash
WIKI_REPO_PATH=../wcs-wiki doppler run -- uv run python -m wiki_curator_cog.main export
```

---

## Observability

Three layers:

1. **Structured logs** — `mini_app_polis` logger throughout, JSON output via `common-python-utils`.
2. **Sentry** — the unhandled exception that ends a run.
3. **Run report** — one per run, sent however it ends: SUCCESS or WARN out of a
   good run, ERROR out of a failed one.

Pipeline-evaluation findings are emitted to `pipeline_evaluations` per run, surfaced in the existing pipeline-evaluations UI on `website-astro-software`.

---

## Key concepts

**The curator does not invent content.** Every wiki claim traces to an upstream source. The schema in `wcs-wiki/CLAUDE.md` is the operating manual; this cog implements it.

**Deterministic by design.** No LLM calls at runtime. Given the same upstream export, the same input produces the same output. The only LLM step in the pipeline is upstream in `transcription-cog`.

**Idempotency.** The render is a pure function of the export it fetched: every page is rewritten each run, derived pages no longer produced are removed, and an unchanged corpus yields an unchanged tree with nothing to commit. There is no per-item skip to get wrong, which is what lets a redundant run cost time rather than correctness.

**Curator version.** Auto-derived at runtime from the package's `pyproject.toml`. semantic-release bumps the version on push to main based on conventional commits: `feat:` → minor, `fix:` → patch, `BREAKING CHANGE:` → major. `chore:` / `docs:` / `style:` commits do not produce a release.

**Single-instance concurrency.** Each run is its own process with its own clone, so there is no shared working tree to race on, and Railway will not start the service again while a previous start is `Active`. Two overlapping runs could still race on the push, where git rejects the loser non-fast-forward (ADR-004).

**Read-only against upstream.** The curator only calls `GET` endpoints for note content. The only `POST` is to `/v1/evaluations` for pipeline-evaluation findings.
