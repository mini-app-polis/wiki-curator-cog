# ADR-001 — Architecture and scope

**Date:** 2026-05-16 (revised 2026-05-18)
**Status:** Accepted

## Context

The `wcs-wiki` repository was created as a parallel view of the existing WCS notes — concept-centric rather than lesson-centric. It needs a curator that reads structured notes from the upstream and writes / updates markdown files in the wiki repo.

The operating spec the curator follows lives in `wcs-wiki/CLAUDE.md`.

## Decision

**Separate cog, not an extension of `transcription-cog`.** The wiki curator has different responsibilities (cross-source organization vs. transcript extraction), different failure modes (page-routing and synthesis vs. transcript parsing), and different concurrency requirements (single-instance vs. per-file). Failure in the curator must not block the notes UI path.

**Two-layer wiki architecture.** Per `wcs-wiki/CLAUDE.md`, the wiki is produced by two layers operating against the same repo:

- **Layer 1 — deterministic collection.** This cog implements it. Reads `notes_json` from the API, routes its fields onto wiki pages mechanically via three alias maps. No LLM at runtime; given the same input the output is byte-identical. Owns source pages, the `## By teacher` paragraphs, the `## Sources` / `## Referenced by` bullets, the index, the log, the views.
- **Layer 2 — LLM polish pass.** Operates on Layer 1's output: prose synthesis, instructor-page background/themes/framings, terminology-page creation when vocabulary collapses are ambiguous, lint, query answering. Runs after Layer 1 in the same Prefect concurrency slot.

**API-only access to upstream.** The curator is an HTTP client of `api-kaianolevine-com`, not a database client. Reads via `GET /v1/wcs/notes/all` (Clerk M2M, `wcs_admin` scope). Writes only to `POST /v1/evaluations` for pipeline-evaluation findings. No direct database connection under any circumstance.

**Markdown in a git repo as the storage substrate.** The wiki is browseable in Obsidian, GitHub web UI, or any markdown viewer. The curator commits one per source ingest plus a final residual commit per run. Schema (`CLAUDE.md`) versioned in the same repo.

**Three operating modes**, dispatched by a router flow (`wiki_curator_router` in `main.py`):

- `backfill` — full-corpus rebuild. Wipes the derived layer (`concepts/`, `techniques/`, `instructors/`, `terminology/`) at start of run, preserving the `_aliases.yaml` files; bypasses the version-equality skip and force-re-ingests every source. Used when shipping curator behavior changes that should reshape the derived layer.
- `incremental` — steady-state, processes notes created since the last run. Honors the version-equality skip. Used downstream of new lesson processing.
- `interactive` — operator-driven, one source at a time. `IngestMode.INTERACTIVE` raises `UnknownNameError` on unknown upstream names rather than auto-adding to `instructors/_aliases.yaml`, so an operator can decide.

**Idempotency via curator version (equality), bypassed in backfill.** The curator version is auto-derived from `pyproject.toml` (`importlib.metadata.version("wiki-curator-cog")`); semantic-release bumps it on every `feat:` / `fix:` push to main. In incremental and interactive modes, ingesting a source whose existing page already matches the running curator version is a no-op. In backfill mode the skip is bypassed unconditionally — the wipe step at start of run would otherwise leave the derived layer permanently empty.

**Three alias maps as the only durable manual surface.** `instructors/_aliases.yaml` maps upstream name variants to canonical instructor slugs (auto-mutated when new names appear in automated / backfill modes). `concepts/_aliases.yaml` and `techniques/_aliases.yaml` map concept and technique variant slugs to canonical slugs (never auto-mutated; manual curation only). All other page content is regenerated from upstream notes_json on every backfill — direct page edits do not survive.

## Consequences

- `notes_json` shape is a hard dependency. Upstream schema bumps become curator concerns; tracked via integration tests against canonical sample payloads.
- Layer 2 (polish pass) will need a coordination mechanism with Layer 1 so synthesized prose (Overview, terminology pages, instructor background) isn't overwritten on the next backfill. Options: separate `.polish.md` companion files Layer 1 ignores, marked regions inside pages that Layer 1 preserves, or per-section frontmatter flags. Deferred until Layer 2 is built.
- Vocabulary collapse is split between mechanical (slugify + depluralize-with-vowel-guards, both in Layer 1) and judgment-call (alias-map entries + terminology pages, both Kaiano-confirmed via Layer 2 proposals). The two halves operate at different times against the same `_aliases.yaml` files.

## Alternatives considered

- **Extension of `transcription-cog`.** Rejected: failure isolation matters more than the small duplication.
- **Direct DB read, API write.** Discussed; rejected for now. Treats the wiki as a real second consumer of the upstream notes domain rather than an internal data pipeline. Costs are bounded; benefits compound if the wiki ever opens up.
- **Single mode with auto-detection (backfill vs. incremental).** Rejected: explicit mode parameter makes intent and ops easier to reason about. Matches `transcription-cog`'s router pattern.
- **One curator with LLM synthesis inline.** Rejected: the two-layer split lets Layer 1 ship and operate without LLM dependency at runtime, makes the deterministic path provably reproducible, and lets Layer 2 evolve at its own cadence.
