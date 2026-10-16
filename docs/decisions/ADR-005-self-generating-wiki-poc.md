# ADR-005 — Self-generating wiki: prove generation and updates before infrastructure

**Date:** 2026-10-16
**Status:** Accepted — planned, not yet implemented
**Supersedes (partially):** ADR-002 ("the renderer does not synthesize"; "polish prose … doesn't write markdown" coordination), ADR-004 (the claim that Layer 2 has no per-item work)
**Related:** `api-kaianolevine-com` ADR-0002, ADR-0003, ADR-0006; Asana MINI-29 and MINI-83 – MINI-88

## Context

The cog renders the canonical entity graph into markdown deterministically and
calls no LLM. Since the Prefect removal (ADR-004), its work has been
operational: runtime, CI, conformance. What it produces has not moved, and the
original intent — a wiki that writes and maintains itself from the lesson
corpus, after Karpathy's "LLM Wiki" — was never built. The Layer 2 polish pass
named in ADR-001 was never designed, and the prose columns ADR-002 put on the
entity rows (`overview_md`, `background_md`, `teaching_themes_md`) have no
writer.

Gaps seen in the wiki so far:

- Links to concepts that had no page, and foundational concepts other pages
  lean on with no page of their own.
- No way for the wiki to evolve: new material and corrections need to reach
  the product through the pipeline, not by hand-editing markdown that the
  next render overwrites.
- Review of proposed changes. A git repo was chosen for branch review; that
  was the simplest mechanism available, not a requirement.

Two infrastructure moves are wanted but deliberately not part of this:
moving the cog to AWS, and serving the wiki on wcs.kaianolevine.com. Both wait
until the concept is proven.

## Decision

### Scope: a local proof of concept on the current runtime

Prove two things — that the wiki can be generated, and that the update process
works — before any infrastructure or product-surface work. No AWS or Railway
changes. Runs are local. The generation code is written runtime-agnostic, one
entity in and one result out, so a Lambda handler can wrap it later without a
rewrite.

### The wiki is a web, and every referenced concept has a page

Concepts are not a hierarchy. Some stand alone, some are referenced by many
others, some reference others, some reference nothing. The structure is
whatever the references make it; nothing declares a concept foundational.

The rule: if a lesson talks about two concepts and how they relate, all three
exist — both entities and the relation — whether or not either concept was
defined. A concept with no definition still gets its page.

- Relations already satisfy this: composition find-or-creates both endpoints
  through `resolve_entity`.
- Skills do not. A drill's purposes and a technique's requirements store the
  skill as text (`skill_name`) and never resolve it, so the skill has no page.
  They are to resolve through `resolve_entity`, keeping the entity id
  alongside the text.
- Every reference renders as a link, and every page gets a `## Referenced by`
  section built from relations, drill purposes and technique requirements.
- A run fails if any rendered link has no page.
- Thin pages are **not** marked as placeholders. A reader sees an
  underdeveloped concept and feeds the pipeline more material.

### Corrections are sources

New lessons and intentional corrections are handled the same way, through the
same ingestion: a dated note — a Doc, a `.txt` or a voice note — in the
watched folder goes through watcher-cog, transcription-cog's extraction and
the API's composition exactly as a lesson does. There is no separate
correction surface for content.

**Newer carries more weight.** Every statement has a date through its source.
When statements conflict, the newer one wins; the older can be marked
superseded rather than deleted.

The API's admin correction endpoints (name corrections, repoint, merge) remain
for rare identity fixes, such as two spellings of one instructor. They are not
the content-correction path.

### Generation integrates; it does not rebuild

An LLM maintains each entity's prose. Its input is the current page as it
stands — including any human edits — plus the statements added since that
page was last integrated. It decides what to keep, merge, revise or mark
superseded. Newer evidence wins.

- **The LLM owns the prose only.** `## By teacher`, `## Sources`, `## Related`
  and `## Referenced by` stay mechanically rendered from the data, so links and
  attribution cannot drift through repeated rewrites.
- **Prose cites its sources** by id, so claims stay traceable across rewrites.
- **A per-entity watermark** records which statements have been integrated.
  A re-run never integrates the same statement twice, and an entity with
  nothing new costs no LLM call.
- **Human edits are dated inputs**, not locks. An edit counts as a correction
  made on its date; a later lesson that contradicts it can win, as it would
  over an older lesson.
- **Rebuild from scratch** stays available: regenerate an entity from all its
  statements with past human edits fed in as dated notes. It is the escape
  hatch for drift, and the way to produce a variant from a subset of sources.

The render remains deterministic over what it reads. The non-determinism lives
in the generation step, and its output is stored, versioned data.

### The API owns the prose and its history

Generated prose and every revision of it live in `api-kaianolevine-com`: the
text, who changed it (LLM or human), when, and for LLM revisions the model and
prompt version. That revision history is the version control.

During the proof of concept the wiki repo stays as the reading, editing and
diffing surface. Before each run the cog compares each page's prose with what
it last wrote; a difference is a human edit, saved to the API as a revision
dated to its commit, and integrated on top of. Once the website exists it
writes edits to the API directly and this sync step goes away. Branch review
is not required.

### Private sources are used, not publicly cited

Private sources (`is_default_visible = false` or visibility `private`) feed
generation. They must not be publicly cited. Scoped wikis were considered;
for now there is one wiki with all data. Citations are stored as source ids so
a public render can drop private ones, and a public-only variant can be
produced through the rebuild path. Because integration builds on the previous
page, private content cannot be extracted from a page after the fact — the
rebuild path is what makes deferring the visibility rule safe, and it is
proven as part of this work, not assumed.

## Plan

Tracked in Asana under MINI-29.

1. **MINI-83** — baseline export and link check.
2. **MINI-84** — resolve skill references to entities; `## Referenced by`.
3. **MINI-85** — notes and corrections ingest as sources. transcription-cog's
   minimum-length gate (`MIN_TRANSCRIPT_CHARS`) currently drops short notes;
   the extraction must emit a correction as a statement that can supersede an
   older one.
4. **MINI-86** — integration-based generation: prompt validated against a
   written rubric, revisions and watermark in the API, edit sync from the
   repo, rebuild path.
5. **MINI-87** — prove the update loop by hand and in tests:
   - a new lesson regenerates only the entities it touches;
   - a correction note contradicting an older claim changes the page;
   - an edit in the repo syncs to the API and survives a later lesson, merged
     with it;
   - a newer note overrides an older edit;
   - nothing new means no LLM calls and nothing to commit;
   - rebuild from scratch works, including a public-only variant.
6. **MINI-88** — full-corpus run; time, cost and quality recorded; go/no-go.

## Consequences

- The cog stops being "deterministic, no LLM at runtime". README and
  PIPELINE.md carry notes pointing here until the code lands.
- The Layer 1 / Layer 2 split of ADR-001, dissolved by ADR-002, returns in a
  different form: mechanical evidence sections and LLM-owned prose on the same
  page, with the prose stored in the API rather than coordinated in markdown.
- Generation is per entity. ADR-004 and `evaluator.yaml` say the Layer 2 pass
  would be a whole-corpus rebuild with no per-item work; that is not how it is
  being built. Each entity with new statements is a distinct unit of work,
  which is what a queue-driven Lambda worker expects. That reading belongs to
  the AWS follow-on; `evaluator.yaml` is not changed until the code is.
- If the markdown repo stops being the edit surface, the git dependency — the
  reason ADR-004's Lambda fit check failed — stops being load-bearing.
- New API surface: skill entity ids on drill purposes and technique
  requirements, a prose revisions table, a per-entity watermark, and a write
  endpoint for the cog.
- Cost becomes a real input: generation calls an LLM per changed entity. The
  watermark keeps steady-state cost proportional to new material.

## Follow-ons, if MINI-88 is a go

Separate work, not part of this decision:

- Move to AWS: a per-entity queue and Lambda worker; retire the Railway
  service, `.railway/` and `railpack.json`.
- Entity pages on wcs.kaianolevine.com, with the citation visibility rule.
- Move the Q&A agent onto the canonical layer (it reads the legacy notes
  tables today).
- Bring `api-kaianolevine-com/docs/wcs-pipeline.md` up to date; it still
  describes render spec v1.0, four views and bucketed sources.

## Alternatives considered

- **Move to AWS first.** Rejected for now: it settles a runtime shape for a
  concept that is not yet proven.
- **Website first.** Rejected for now for the same reason; the product surface
  waits on content worth showing.
- **A concept hierarchy with top-level parent pages.** Rejected: the domain is
  a web of references, not a tree.
- **Mark placeholder pages.** Rejected: a thin page already says it is thin.
- **A separate correction surface** (CLI, guidance notes, prose
  status/approval). Rejected: corrections are sources and share the
  ingestion, with recency deciding conflicts.
- **Regenerate prose from scratch every run.** Rejected as the default: it
  discards human edits and makes each page a fresh guess. Kept as the rebuild
  escape hatch.
- **Make the markdown repo the store of prose.** Rejected: the API owns the
  data, and the website will read and write there.
- **Scoped wikis, one per audience.** Deferred: one wiki with all data now;
  citations stored so a public variant can be derived.
