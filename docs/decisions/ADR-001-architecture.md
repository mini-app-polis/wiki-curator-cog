# ADR-001 — Architecture and scope

**Date:** 2026-05-16
**Status:** Accepted

## Context

The `wcs-wiki` repository was created as a parallel view of the existing WCS notes — concept-centric rather than lesson-centric. It needs a curator that reads structured notes from the upstream and writes/updates markdown files in the wiki repo.

The design conversation that produced this cog is recorded in the project history; the operating manual the curator follows lives in `wcs-wiki/CLAUDE.md` v0.5.

## Decision

**Separate cog, not an extension of `transcription-cog`.** The wiki curator has different responsibilities (synthesis vs. extraction), different failure modes (LLM judgment calls vs. transcript parsing), and different concurrency requirements (single-instance vs. per-file). Failure in the curator must not block the notes UI path.

**API-only access to upstream.** The curator is an HTTP client of `api-kaianolevine-com`, not a database client. Reads via `GET /v1/wcs/notes/all` (Clerk M2M, `wcs_admin` scope). Writes only to `POST /v1/evaluations` for pipeline-evaluation findings. No direct database connection under any circumstance.

**Markdown in a git repo as the storage substrate.** The wiki is browseable in Obsidian, GitHub web UI, or any markdown viewer. Curator commits one-per-source. Schema (`CLAUDE.md`) versioned in the same repo.

**Three operating modes**, dispatched by a router flow:

- `backfill` — one-time corpus run, processes every existing note in chronological order.
- `incremental` — steady-state, processes notes created since the last run (Phase 2).
- `interactive` (future) — Kaiano-in-the-loop ingest, primarily for Claude Code sessions; not a Prefect flow.

**Idempotency via `WIKI_CURATOR_VERSION`.** Ingesting the same `note_id` at the same curator version is a no-op. Bumping the version triggers reprocessing.

## Consequences

- Phase 1.5 work on `api-kaianolevine-com`: add a `since` filter to `GET /v1/wcs/notes/all` before incremental mode is useful.
- Phase 2 trigger: downstream of `transcription-cog` completion, event-driven, not scheduled.
- `notes_json` shape is a hard dependency. Upstream schema bumps become curator concerns; tracked via the schema version field in `notes_json` (if/when one is added) or by integration tests against canonical sample payloads.

## Alternatives considered

- **Extension of `transcription-cog`.** Rejected: failure isolation matters more than the small duplication.
- **Direct DB read, API write.** Discussed; rejected for now. Treats the wiki as a real second consumer of the upstream notes domain rather than an internal data pipeline. Costs are bounded (Phase 1.5 work is small); benefits compound if the wiki ever opens up.
- **Single mode with auto-detection (backfill vs. incremental).** Rejected: explicit mode parameter makes intent and ops easier to reason about. Matches `transcription-cog`'s router pattern.
