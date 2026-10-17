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
WIKI_REPO_PATH=../wcs-wiki doppler run --config prd -- uv run python -m wiki_curator_cog.main render --links links.json > render-summary.json
```

### Link locally (ADR-005, MINI-84)

The linking pass, local half: reads the export, asks an LLM which concepts
each statement mentions, and writes the references to `links.json` — never to
the API. `links.json` is also the watermark: a statement already linked under
the current linker version is skipped on the next run. A readable
`links-review.md` is written next to it for the statements that run covered.
Both are git-ignored; keep `links.json`, since it holds what the calls cost.

```bash
# a reproducible sample of 20, spread across statement kinds
doppler run --config prd -- uv run python -m wiki_curator_cog.main link --sample 20
```

Needs `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=openai` and `OPENAI_API_KEY`);
`LLM_MODEL` overrides the model.

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
