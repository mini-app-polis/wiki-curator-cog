# ADR-003 — Flat sources directory; views replace bucketed routing

**Date:** 2026-05-29
**Status:** Accepted
**Supersedes (partially):** ADR-002 (which referenced bucketed source directories per the v0.5 layout)
**Related:** wcs-wiki/CLAUDE.md v1.1

## Context

The original wiki layout bucketed source pages by primary instructor: `sources/kaiano/`, `sources/kate/`, `sources/robert/`, `sources/external/`. The renderer chose a bucket per source by intersecting the source's canonical-instructor slugs with a hardcoded set `BUCKETED_INSTRUCTORS = ("kaiano", "kate", "robert")`.

Three problems with this scheme:

1. **The bucket list was wrong.** Kate is never an instructor in the corpus — she is consistently a student. Robert's actual canonical slug after substrate normalization is `robert-royston`, which never matches the hardcoded `robert`. The bucketing produced no Kate sources (correct: there are none) and no Robert sources in the bucket directory (incorrect: Robert-taught sources existed but got routed to `external/` because of the slug mismatch).

2. **It conflated two concerns.** Filesystem layout (where files live) and browseable filtering (how a user finds related sources) were both encoded into the same mechanism. A user wanting "all of Kaiano's lessons" had to remember to look in `sources/kaiano/`. A user wanting "all classes Kate took" had no surface at all.

3. **It made routing decisions on data the substrate considers fluid.** When a source is recategorized — for example, a private lesson reclassified as co-taught — its bucket can change, requiring the renderer to move the file and rewrite every wiki-link pointing to it. The renderer's `list_stale_derived_paths` handles the file removal, but the move-and-rewrite is structural churn for no informational gain.

## Decision

**Sources live in a flat `sources/<slug>.md` directory.** The slug already begins with the source's session date, so filesystem ordering is naturally chronological. No subdirectories. No primary-instructor concept.

**Browseable filtering happens through `views/`.** Each view is a curated tour: a render-time-computed page that lists sources matching a filter (e.g., "instructors contains kaiano"), with prose context and links to entity and source pages. Views are derived files like everything else in the wiki — regenerated every render run from `source.instructors_raw` / `source.students_raw` and a small spec table (`REQUIRED_VIEWS`).

**The v0 view set is:**

- `kaianos-canon` — sources where Kaiano is an instructor.
- `kate-as-student` — sources where Kate is a student.
- `full-model` — the whole corpus, chronologically.

Earlier view ideas (`kaiano-teaching-kate`, `roberts-canon`) were dropped: the first is a strict subset of `kate-as-student` and `kaianos-canon`, and the second has no matching sources in the current corpus. Adding views is a code change in `REQUIRED_VIEWS` plus a re-deploy — cheap when a new question genuinely benefits from a first-class view.

## Consequences

**Easier:**

- Source recategorization (e.g., "this was actually a private lesson, not a workshop") no longer changes file paths. The slug is stable; metadata changes update the source's frontmatter and any view's membership, but the path on disk stays the same.
- The renderer becomes simpler: no `BUCKETED_INSTRUCTORS`, no `_bucket_for_instructors`, no `source_bucket_by_id` index, no bucketed wiki-link helper. Roughly 40 lines deleted across `render.py` and the corresponding test file.
- Adding a new "I want to find sources where X" answer is a view, not a directory layout change. The mental model is cleaner: filesystem is content; views are queries.
- The Obsidian graph view treats sources as a flat layer cleanly, with edges from concepts/techniques/drills/instructors to sources via wiki-links. Bucketed subdirectories made some graph layouts visually misleading by clustering same-bucket sources artificially.

**Harder:**

- Existing wiki-links from external sources (commits, issues, slack) that pointed at `sources/kaiano/<slug>` now 404. There were few — the wiki has not been a stable surface — but this is a hard break. Fixed by either redirects (not applicable for markdown / GitHub Pages-style rendering) or by accepting the break.
- The flat sources directory grows monotonically. At current corpus size (90 sources) the directory listing is manageable; at 500+ sources, browsing the raw directory becomes noisy. Mitigation: views are intended to be the primary navigation surface, not filesystem browsing.
- "Find sources from instructor X" requires opening a view file, not scrolling a folder. For users who browse via filesystem rather than via Obsidian's vault, this is a small workflow shift.

**Trade-offs accepted:**

- We accept the wiki-link break in favor of a layout that won't change again. The bucketing scheme would have needed re-revision every time the corpus gained a new bucketed instructor; the flat scheme is stable forever.
- We accept that views are code-defined, not user-defined. A future change could load `REQUIRED_VIEWS` from a YAML file in the wiki repo so users could add views without touching the cog, but the current view set is small and stable enough that the YAML layer would be premature.

## Implementation

- `src/wiki_curator_cog/render.py`: bucketing constants, helpers, and index field removed. `_source_link` simplified to produce `[[sources/<slug>]]`. `render_bundle`'s source-page path construction emits `sources/<slug>.md`. `DERIVED_GLOBS` updated from `sources/**/*.md` to `sources/*.md`.
- `REQUIRED_VIEWS` updated to the three views above. `_ViewSpec` mechanism unchanged — it already supported instructors_includes / students_includes filtering.
- `tests/wiki_curator_cog/test_render.py`: `test_source_pages_bucket_by_primary_instructor` renamed and rewritten as `test_source_pages_land_in_flat_sources_directory`. New test `test_instructor_page_includes_coauth_attributions_with_null_instructor_id` verifies the renderer's co-taught attribution derivation (the other half of the work that landed in the same release; see `api-kaianolevine-com` ADR-0007).
- `wcs-wiki/CLAUDE.md` bumped from v1.0 to v1.1. The Repository Layout section and the Views section in that spec are the human-facing description of this decision.
