# ADR-002 — Repurpose curator as renderer over canonical entities

**Date:** 2026-05-19
**Status:** Accepted
**Supersedes (partially):** ADR-001 (Architecture and scope)
**Related:** `api-kaianolevine-com` ADR-0002 (WCS entity substrate), ADR-0004 (rebuild from scratch)

## Context

ADR-001 of this repo established the two-layer wiki architecture: Layer 1 (this cog) deterministically routes `notes_json` fields onto markdown pages; Layer 2 (deferred) is an LLM polish pass over Layer 1's output. The wiki repo was treated as the system of record for the conceptual model; this cog was its maintainer.

`api-kaianolevine-com` ADR-0002 commits to a different architecture: the canonical representation of WCS knowledge is a normalized entity graph in Postgres. The wiki is no longer a system of record — it is a view, projected as markdown for Obsidian browsing.

This invalidates the foundational decisions in ADR-001:

- "Two-layer wiki architecture" with L1 mechanical pass and L2 polish pass — there is no Layer 1 mechanical pass anymore. Canonical structure exists in Postgres; this cog renders it. Polish prose lives on entity rows in the canonical store; it survives every render without an L1↔L2 coordination protocol.
- "API-only access to upstream" — preserved, but the upstream changes. Reads switch from `GET /v1/wcs/notes/all` to entity-shaped read endpoints (`GET /v1/wcs/wiki/concepts/{slug}`, etc., or a bulk export endpoint).
- "Three operating modes (backfill, incremental, interactive)" — collapses. The cog is now a renderer; "backfill" and "incremental" become "regenerate the markdown bundle." There is no extraction state to manage, no idempotency check against `curator_version`, no wipe-and-rebuild logic.
- "Three alias maps as the only durable manual surface" — moves entirely. Alias maps (`instructors/_aliases.yaml`, `concepts/_aliases.yaml`, `techniques/_aliases.yaml`) become entity-alias and instructor-alias tables in the canonical store. No YAML files in the wiki repo.

The substantial design work that lives in this cog today — quality filters (`_passes_concept_filter`, `_passes_instructor_filter`, the loose-acronym detector, dance-style/event-suffix blocklists), the alias machinery, the synthesis logic (`_synthesize_page`), the wipe-and-rebuild backfill — is either superseded (filters move upstream to extraction-time validation) or eliminated (synthesis is replaced by joined SQL; backfill becomes a re-render).

The cog still curates: it composes canonical entity data into a coherent markdown projection, decides page structure and cross-references, and orders content for human readability. What changes is *what it curates from*. It no longer curates from raw `notes_json` blobs — it curates from a canonical entity graph. The name `wiki-curator-cog` remains accurate; the role's substrate is what shifts.

## Decision

**Repurpose this cog as a renderer over the canonical entity store.** It reads from `api-kaianolevine-com` via the API and renders markdown to the `wcs-wiki` repo. It does not maintain state. It does not enforce identity. It does not synthesize. It curates the projection of canonical data into markdown form.

The repo name remains `wiki-curator-cog`. The role is still curation — selecting, organizing, and presenting WCS knowledge in a coherent browseable form. What changes is the substrate it curates from: previously raw notes_json, now the canonical entity graph.

### Scope of the renderer

The renderer's job:

1. Authenticate to `api-kaianolevine-com` via Clerk M2M (unchanged).
2. Clone or refresh the `wcs-wiki` repo at `WIKI_REPO_PATH` (unchanged).
3. Fetch the full entity corpus from a bulk-export endpoint (or paginate per-kind endpoints).
4. Render each entity to its markdown page per the render spec (see `wcs-wiki/CLAUDE.md`).
5. Render the index, the log entry, and the required views.
6. Commit the regenerated wiki to git and push (one residual commit per run, not one per source).

The renderer has no idempotency contract beyond "given the same canonical state, produce the same markdown." Re-running is always safe. There is no `curator_version` equality check; the canonical state is the version, and the renderer renders whatever it is.

### What survives from the existing cog

The following pieces of the current codebase are reusable in the renderer:

- **`boot.py`** — clone-or-refresh of the wiki repo on Railway ephemeral filesystem. Push reliability tuning (HTTP/1.1, postBuffer=500MB). Token injection. All preserved.
- **`git_ops.py`** — `WikiRepo` wrapper, push-with-retry logic. Preserved.
- **`markdown_utils.py`** — markdown parser/serializer with frontmatter and section helpers. Preserved; the renderer uses it to emit markdown.
- **`config.py`** — config loading, with adjustments (no `curator_version`, no `state_path`, simpler).
- **`main.py`** — entrypoint pattern; simplified to one production flow (`export`) plus the existing `regenerate-views` utility (which becomes redundant once `export` itself regenerates views every time).
- **The Prefect deployment shape and concurrency slot.** A single-instance flow that exports the wiki, scheduled or triggered on demand.

### What goes away

The following pieces are eliminated:

- **`curator.py`** — `ingest_one_source` and its idempotency machinery. There is no per-source ingest; the renderer renders the whole wiki at once from canonical state.
- **`derived_pages.py`** — all 1,400 lines. Quality filters, contribution planning, attribution upserts, structural synthesis, wipe-and-rebuild. Replaced by render logic that reads canonical data and emits markdown.
- **`aliases.py`** — the `AliasMap` class for instructor/concept/technique alias YAML files. Aliases live in the canonical store; no local mutation.
- **`bootstrap_aliases.py`** — instructor-alias clustering utility. Functionality (if still useful) becomes an admin endpoint on the API operating against the `instructor_aliases` table.
- **`inventory.py`** — wiki state inventory by scanning frontmatter. The canonical store is the inventory; no scan needed.
- **`slugs.py`** — `canonicalize_names`, `canonicalize_concept_slug`, `canonicalize_technique_slug`, `bucket_for_instructors`, depluralization rules. All moves upstream: slugification and depluralization to the API's entity-resolution logic; bucketing to render-time grouping in the renderer.
- **`state.py`** — `CuratorState` with `last_run_at` / `curator_version_at_last_run`. The renderer has no per-run state.
- **`views.py`** — `regenerate_views` for the four required views. Survives in spirit (the renderer still produces the four views) but the logic simplifies dramatically — it reads from API endpoints rather than scanning source frontmatter.
- **`wiki_files.py`** — index upsert and log append, since both index and log are regenerated whole on every export (the index is computed from the canonical state; the log records the export event with summary stats).

### Repo name retained

The repo name remains `wiki-curator-cog`. "Curator" continues to describe the role accurately — selecting, organizing, and presenting WCS knowledge in coherent markdown form. The cog is curating from a different substrate (canonical entity graph rather than raw notes_json), but the function is recognizably curation.

Keeping the name avoids one-time migration cost (GitHub URL, CI badges, Railway service rewiring, package import name updates across `pyproject.toml`/src/tests). The cost-benefit comes out cleanly in favor of retention given that the role can be honestly described with the existing name.

ADR-001 of this repo, including the discussion of the curator's two-layer model, the alias maps in the repo, the wipe-and-rebuild backfill, and the three operating modes, is superseded by this ADR and by `api-kaianolevine-com` ADR-0002. It is preserved as a historical record; future readers should treat it as documenting "what we tried first."

### Two-layer model: dissolved

ADR-001's two-layer split (L1 deterministic / L2 polish) does not apply to the new architecture. The reasoning:

- **There is no L1 mechanical pass.** Canonical structure exists in the entity store. The renderer renders it.
- **Polish prose lives on entity rows.** `entities.overview_md`, `instructors.background_md`, `instructors.teaching_themes_md`, etc. are columns on canonical rows. The polish pass (when it ships) writes to these columns. The renderer renders them when present.
- **No coordination protocol.** Re-rendering the wiki never destroys polish prose because polish prose isn't *in* the rendered output — it's in the entity store. The render just reads it.

The polish pass is now a separate concern from the renderer. It's a future LLM workflow (likely an API-side job, possibly a separate cog or a flow under this renderer cog's deployment) that operates on the canonical store and writes prose columns. It doesn't write markdown.

## Consequences

### What this simplifies

- **The cog drops to ~500 lines.** No filters, no synthesis, no idempotency machinery, no alias mutation, no wipe-and-rebuild. Just: fetch canonical state, render markdown, commit, push.
- **No more L1/L2 coordination problem.** Polish prose persists; renders never destroy it.
- **No more `curator_version` skip logic.** Re-export is always safe and always cheap. Trigger on schedule, on demand, or downstream of canonical-state changes.
- **No more local-file state.** The cog has no `_aliases.yaml` files to maintain, no `state.json` to persist. Everything is read from the API.
- **Predictable wiki output.** Given the same canonical state at the same render-spec version, the renderer produces byte-identical markdown. Diffing wiki commits shows real semantic changes only.

### What this complicates

- **The renderer depends on the API more deeply.** Today's cog only depends on `/v1/wcs/notes/all` and `/v1/evaluations`. The renderer depends on richer read endpoints. API availability becomes a harder dependency. Mitigation: the API is already a hard dependency; the cog can't run without it regardless.
- **Bulk-export endpoint design matters.** Fetching the entire canonical graph in N small calls is inefficient; one bulk endpoint is needed. The shape of that endpoint constrains the renderer's data flow. Mitigation: design the bulk endpoint deliberately (paginated by kind, with a single roll-up call for the index/log/views render).
- **Renaming has migration cost.** Bounded but real. (See "Renaming the repo" above.)

### What this enables

- **The wiki is regenerable on demand.** No state to corrupt, no per-source incremental logic that might drift. If the wiki repo ever diverges from canonical state, an `export` run reconciles it.
- **The wiki render spec evolves independently of the canonical store.** Want to change how concept pages look? Update the render spec in `wcs-wiki/CLAUDE.md`, update the renderer's render code, re-export. No data migration.
- **Polish prose is durable.** Future Layer 2 work (LLM-written Overview, Background, Teaching themes) writes to entity columns. Re-exports include the polish. Re-extractions of underlying sources don't disturb it.
- **The wiki's role is finally clear.** Markdown projection of the entity store, for Obsidian browsing and human readability. Not a system of record. Not an editing surface.

## Alternatives considered

**Rename the repo to `wiki-renderer-cog`** to reflect the simpler render-only role. Considered. The new name would be more descriptive of the implementation (the cog no longer does deterministic fanout, alias mutation, or wipe-and-rebuild). Rejected because: (a) "curator" is not inaccurate — the cog still selects, organizes, and presents WCS knowledge in coherent form, just from a different substrate; (b) the rename cost (GitHub URL, CI badges, Railway service rewiring, package import name across `pyproject.toml`/src/tests) is one-time but real; (c) future readers landing in the repo will find the ADRs explaining what curator means in this context. The bar for renaming was "the existing name is misleading"; the existing name is unspecific but not misleading.

**Roll the rendering into the API itself** (`POST /v1/wcs/wiki/export` writes directly to the wiki repo). Rejected for proof-of-concept. The cog has working git operations, push reliability tuning, and a Railway deployment shape. Reusing them is cheaper than re-implementing git push in the API. If the rendering ever needs to be triggered from the API (e.g., automatically after a correction is applied), the API can call a Prefect deployment of the curator.

**Keep the alias maps in the wiki repo** as the operator-editing surface, with the API treating them as authoritative. Rejected. Two systems of record for alias data (the repo file and the entity store) requires synchronization, which always drifts. Aliases live in the entity store; corrections are an API call.
