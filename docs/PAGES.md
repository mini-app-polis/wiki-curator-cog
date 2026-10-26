# wcs-wiki page spec

This document is the render spec for the `wcs-wiki` repository. It tells the renderer (this repository, `wiki-curator-cog`) how to project the canonical WCS entity graph — owned by `api-kaianolevine-com` — into the markdown pages of this wiki. Kaiano is the human owner.

The wiki is **derived**, not authoritative. The system of record is the entity substrate in `api-kaianolevine-com`, surfaced for lessons at `wcs.kaianolevine.com/notes`. This wiki is a parallel, concept-organized projection of that same canonical data, built for Obsidian browsing.

Per [ADR-0002](decisions/ADR-0002-curate-from-canonical-entities.md), the curator is a **renderer**: it does not maintain state, does not enforce identity, and does not synthesize. Canonical structure — entities, their kinds, their resolved names and aliases, their overviews — already exists in the substrate. The renderer reads it and emits markdown. Given the same canonical state at the same render-spec version, the renderer produces byte-identical output.

> **Spec status — 2026-10-16.** v1.2 describes the renderer as it runs today.
> [ADR-0005](decisions/ADR-0005-self-generating-wiki-poc.md) changes it (local half built, 2026-10-16): an
> LLM linking pass adds references between entities, giving every page
> `## Mentions` and `## Referenced by` and general concepts (connection,
> timing, leading, following) pages of their own; an LLM generation step
> writes each entity's `## Overview` as a short plain definition plus an
> optional `## In practice` paragraph, built on the page as it stands; and
> notes can merge two names into one page (`alias_of`) at build time. A
> consolidation pass folds narrow entities into the broader concept they
> detail (`facet_of`, MINI-93): the facet's page goes and it is listed, nested,
> under the broader page's `## Details`; notes override it. Curator state —
> references, facet decisions, prose and its revisions — is held by the API
> (MINI-89, MINI-94); `.curator/` keeps only a local cache of it and the
> reviews, all git-ignored, including the private `thin-review.md`. The spec
> will be revised when the page documents land (MINI-96). Also stale
> below: the runtime (Prefect is gone — one-shot process, [ADR-0004](decisions/ADR-0004-one-shot-on-railway-no-prefect.md)), the auth
> (a named machine API key, not Clerk M2M), and "four views" (there are three).

---

## Upstream architecture

The renderer does not read raw transcripts and does not query any database directly. It consumes the canonical entity graph from the `api-kaianolevine-com` HTTP API. Transcript → extraction → entity resolution → composition has already happened upstream; the renderer sits at the end of the chain and treats the API as its only input.

```
Drive transcript drop
        │
        ▼
transcription-cog ──► api-kaianolevine-com  (composition → canonical entity graph)
                                │
                                ▼ (HTTP API, read-only, bulk export)
                        wiki-curator-cog (renderer)
                                │
                                ▼
                          wcs-wiki (this repo)
```

The renderer fetches the entire visible corpus in one call:

```
GET /v1/wcs/wiki/export
```

This returns a single `WcsWikiExportItem` containing nine arrays. The renderer joins them by id at render time; it never makes per-entity follow-up calls.

**Identity is upstream.** The substrate has already resolved entities, assigned canonical names and slugs, attached aliases, and classified each entity by kind. The renderer does **not** re-derive any of this. It does not collapse vocabulary, merge near-duplicates, decide concept-vs-technique, or canonicalize instructor names. Those are the substrate's job (and, for fragmentation the substrate left conservative, an admin-side curation task against the substrate's alias/merge layer — never a wiki concern). If two entities look like they should be one, that is feedback for the upstream alias layer, surfaced in the run log, not something the renderer fixes by hand.

**Prose columns are upstream too.** Entity `overview_md`, instructor `background_md` / `teaching_themes_md` / `notable_framings_md` are authored columns on canonical rows (today possibly empty; eventually filled by a separate polish pass). The renderer renders them when present and omits the section when empty. The renderer never writes its own overview prose.

---

## The export shape

`GET /v1/wcs/wiki/export` → `WcsWikiExportItem`:

### `entities[]` — the canonical knowledge nodes

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | UUID | Entity identifier. |
| `slug` | str | Canonical, lowercase, hyphenated. The page filename. |
| `canonical_name` | str | Display name. |
| `kind` | str | One of `concept`, `technique`, `pattern`, `drill`. Determines the page directory. |
| `overview_md` | str | Server-authored neutral overview. May be empty. |
| `status` | str | `stub` / `draft` / `mature` (or substrate equivalent). Mirror into frontmatter. |
| `external_origin` | dict | Provenance for entities sourced from outside lessons. May be empty. |
| `aliases` | list[str] | Variant names. Render into the page's `aliases` frontmatter; do not create separate pages for them. |

### `instructors[]` — teaching authorities

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | UUID | Instructor identifier. |
| `slug` | str | Canonical slug; the instructor page filename. |
| `canonical_name` | str | Display name. |
| `background_md` | str | Server-authored. May be empty. |
| `teaching_themes_md` | str | Server-authored. May be empty. |
| `notable_framings_md` | str | Server-authored. May be empty. |
| `aliases` | list[str] | Variant names. |

Instructors are only the resolved teaching authorities (those who taught a source, or were attributed a definition/attribution). People merely *mentioned* in a lesson are **not** instructors — they live in `references[]` as raw names (see below).

### `sources[]` — the lessons

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | UUID | Source identifier. |
| `transcript_id` | UUID | Upstream transcript link. |
| `title` | str \| null | Topic/display title. |
| `session_date` | date \| null | ISO-8601. |
| `session_type` | str | `private_lesson`, `group_class`, `workshop`, `other`, … |
| `instructors_raw` | list[str] | Verbatim instructor names from the filename. |
| `students_raw` | list[str] | Verbatim student names from the filename. |
| `organization` | str | Event/studio context. May be "". |
| `visibility` | str | `private` / `public`. |
| `is_default_visible` | bool | Catalog default-visibility flag. |
| `created_at` | datetime | Row creation time. |

`instructors_raw` / `students_raw` are the filename-authoritative names. The renderer maps each to its canonical instructor by matching against the instructors array (and their aliases) where possible; unmatched names render verbatim.

### Per-source content rows

These are the substance. Each carries a `source_id`, most carry an `instructor_id` (nullable), and entity-linked rows carry an `entity_id` (or `*_entity_id`). The renderer groups them by entity (for entity pages) and by source (for source pages).

**`attributions[]`** — the primary teaching content. What an instructor taught about an entity in a source.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | UUID | |
| `source_id` | UUID | Source it came from. |
| `instructor_id` | UUID \| null | Attributed teaching authority. |
| `attribution_kind` | str | e.g. `taught`, `demonstrated`, `drilled`, `mistake`, `correction`. |
| `prose` | str | The teaching content, in prose. The main renderable text. |
| `raw_term` | str | The entity as named in the lesson. |
| `position` | int | Order within the source. |
| `drill_goal` | str \| null | If a drill attribution. |
| `drill_steps` | list[str] \| null | If a drill attribution. |
| `mistake_text` | str \| null | If a mistake/correction attribution. |
| `correction_text` | str \| null | If a mistake/correction attribution. |
| `origin` | str | `extraction` / correction / addition provenance. |

Note: attributions reference their entity via the upstream graph, but the **entity_id is resolved by the renderer through the attribution's relationship to entities** — see "Joining" below. The human-readable `raw_term` is always present as a fallback label.

**`definitions[]`** — vocabulary. A term + its definition, as defined in a source.

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `entity_id`, `source_id`, `instructor_id` | | links |
| `term` | str | The term. |
| `definition` | str | The definition prose. |
| `position` | int | |
| `origin` | str | |

**`relations[]`** — edges between entities.

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `from_entity_id`, `to_entity_id`, `source_id` | | links |
| `relation_kind` | str | Free-string kind (e.g. `prerequisite-of`, `variation-of`, `contrasts-with`). |
| `prose` | str | The relation described in prose. |
| `origin` | str | |

**`drill_purposes[]`** — what skill a drill develops.

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `drill_entity_id`, `source_id` | | links (entity is the drill) |
| `skill_name` | str | The skill the drill trains. |
| `skill_slug` | str | Slug for the skill. |
| `prose` | str | Description. |
| `focus_context` | str | When/why to focus on it. |
| `origin` | str | |

**`technique_requirements[]`** — what skill a technique requires.

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `technique_entity_id`, `source_id` | | links (entity is the technique) |
| `skill_name`, `skill_slug` | str | The required skill. |
| `prose` | str | Description. |
| `origin` | str | |

**`references[]`** — people mentioned in a source, stored as raw names. **Not linked to canonical instructors** (first-name-only mentions are too sparse to resolve; this is a deliberate upstream decision).

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `source_id` | | links |
| `referenced_name` | str | The name as mentioned. |
| `context` | str | What was said about them. |
| `ref_type` | str | `instructor` / `pro` / `dancer` / … as classified upstream. |
| `origin` | str | |
| `created_at` | datetime | |

When a field is missing or an array is empty, the source simply did not contain that kind of content. Never invent content to fill gaps.

---

## Joining the export

The renderer builds these in-memory indexes once per run, then renders from them:

- `entities_by_id` and `entities_by_slug`.
- `instructors_by_id`.
- `sources_by_id`.
- `attributions_by_entity` — attributions grouped by their entity, for entity-page `## By teacher`.
- `attributions_by_source` — attributions grouped by source, for source pages.
- `definitions_by_entity`, `definitions_by_source`.
- `relations_by_entity` (both `from` and `to` sides).
- `drill_purposes_by_entity` (keyed by `drill_entity_id`), `technique_requirements_by_entity` (keyed by `technique_entity_id`).
- `references_by_source`.

Within each entity's grouped content, sub-group by `instructor_id` for the `## By teacher` structure. Resolve `instructor_id` → instructor `canonical_name` via `instructors_by_id`. For attributions whose `instructor_id` is null, attribute to the source's `instructors_raw` (the source-level teaching authority).

---

## Entity → page mapping

The single most important mapping. Each canonical entity becomes exactly one page, in the directory chosen by its `kind`:

| `kind` | Directory | Page type |
| --- | --- | --- |
| `concept` | `concepts/` | Concept page |
| `technique` | `techniques/` | Technique page |
| `pattern` | `patterns/` | Pattern page |
| `drill` | `drills/` | Drill page |

The renderer no longer *decides* concept-vs-technique — `kind` is set upstream at entity-resolution time. Render by kind, no heuristics.

Per-source content rows attach to pages as follows:

| Export array | Renders onto | As |
| --- | --- | --- |
| `attributions` | the attributed entity's page | `## By teacher` subsections (grouped by instructor), plus `## Common mistakes` for `attribution_kind` in {mistake, correction} |
| `definitions` | the defined entity's page | `## Definitions` / vocabulary, attributed to source/instructor |
| `relations` | both endpoint entities' pages | `## Related` cross-links, with the relation prose |
| `drill_purposes` | the drill entity's page | `## Develops` (skills this drill trains) |
| `technique_requirements` | the technique entity's page | `## Requires` (skills this technique needs) |
| `references` | source pages (and, where `ref_type` warrants, surfaced in the run log) | source page `## References`, rendered as raw `referenced_name` + `ref_type` + `context`. **Never** create or update instructor pages from references — references are not linked to canonical instructors. |

`overview_md`, `background_md`, `teaching_themes_md`, `notable_framings_md` render directly as their respective sections when non-empty.

---

## Operating posture

**The wiki is descriptive, not prescriptive.** It faithfully represents what each source says, including disagreements. Every claim traces to a source.

**Kaiano-as-teacher is a source like any other.** When a source's instructor is Kaiano, claims from it are attributed to Kaiano identically to Kate's or Robert's. A group class Kaiano taught and a private lesson Kaiano taught both contribute to "Kaiano's canon" the same way.

**Disagreements are content, not problems to resolve.** When sources differ on an entity, the page shows both positions with attribution. The renderer does not adjudicate. Divergence surfaces naturally in the `## By teacher` structure (different teachers, different subsections) and via `relations` of a contrasting kind.

**The renderer never modifies upstream data.** The API exposes the export read-only. If the rendered output reveals a data problem (fragmented entities, a mis-resolved name, an empty overview that should be filled), that is feedback for the substrate / its admin curation surface, recorded in the run log — not fixed by hand in the wiki.

**The renderer renders the full corpus.** It processes every entity, source, instructor, and content row returned by the export. The export is already visibility-scoped server-side to the renderer's credentials; the renderer does not filter further. `is_default_visible` and `visibility` are mirrored into source-page frontmatter so post-hoc filtering is possible without re-render.

**Stateless and deterministic.** No `curator_version` skip logic, no inventory scan, no per-source idempotency check. Re-rendering is always safe and always full. Given the same canonical state at the same render-spec version, output is byte-identical.

---

## Repository layout

```
wcs-wiki/
├── CLAUDE.md                # points Claude sessions at this spec
├── README.md                # human-facing repo overview
├── index.md                 # catalog of all pages (regenerated whole each run)
├── log.md                   # chronological render log (append-only)
├── sources/                 # one page per source, flat layout
├── concepts/                # kind=concept entities
├── techniques/              # kind=technique entities
├── patterns/                # kind=pattern entities (sugar push, whip, etc.)
├── drills/                  # kind=drill entities
├── instructors/             # one page per canonical instructor (incl. Kaiano)
└── views/                   # pre-rendered filtered slices
```

No `_aliases.yaml` files anywhere — aliases live on canonical rows and render into frontmatter. The `terminology/` directory from the old layout is dropped; cross-instructor vocabulary divergence now surfaces inside entity pages' `## By teacher` structure and via `relations`, both of which are upstream-resolved.

**Source layout.** Sources live in a flat directory keyed by their slug:
`sources/<session-date>-<title-or-instructor>.md`. Filesystem ordering is
chronological because the slug is date-prefixed. Instructor-centric and
student-centric browsing happens through `views/` (see below), not through
directory structure.

---

## Page types

### Source pages (`sources/<slug>.md`)

One page per source. Filename slug: `<session_date>-<title-or-instructor-slug>`.

**Frontmatter:**

```yaml
---
type: source
source_id: <UUID>
transcript_id: <UUID>
session_type: <session_type>
instructors: [<canonical-slug>, ...]
instructors_raw: [<verbatim>, ...]
students_raw: [<verbatim>, ...]
organization: <string>
session_date: YYYY-MM-DD
title: <string or null>
is_default_visible: <bool>
visibility: <private|public>
---
```

**Body (omit empty sections):**

1. `## Metadata` — date, type, instructors, students, organization (a compact rendering of frontmatter for readers browsing on GitHub where frontmatter isn't shown).
2. `## Taught` — the source's `attributions` (kind `taught`/`demonstrated`/`drilled`), each rendered from `prose`, headed by `raw_term`, linked to the entity page when the entity is known. Include `drill_goal` / `drill_steps` as sub-detail where present.
3. `## Definitions` — the source's `definitions`, `term` + `definition`.
4. `## Common mistakes` — `attributions` of kind `mistake`/`correction`, rendering `mistake_text` → `correction_text`.
5. `## Relations` — the source's `relations`, prose with `relation_kind` label.
6. `## References` — the source's `references`: `referenced_name` (`ref_type`) — `context`.

### Entity pages — concepts, techniques, patterns, drills

One page per canonical entity, chosen by `kind`:

- `concepts/<slug>.md` for `kind: concept`
- `techniques/<slug>.md` for `kind: technique`
- `patterns/<slug>.md` for `kind: pattern`
- `drills/<slug>.md` for `kind: drill`

The frontmatter `type:` field equals the kind directly (no longer collapsing pattern to technique).

**Frontmatter:**

```yaml
---
type: concept | technique | pattern | drill
slug: <slug>
canonical_name: <name>
kind: <kind>
aliases: [<from entity.aliases>]
status: <entity.status>
teachers: [<instructor slugs with attributions/definitions here>]
sources: [<source slugs that contributed>]
related: [<slugs from relations>]
---
```

**Body (omit empty sections):**

1. `## Overview` — render `entity.overview_md` verbatim. Omit if empty.
2. `## By teacher` — one `### <Instructor>` subsection per instructor with attributions/definitions for this entity. Within each, render the attribution `prose` (and definitions' `term`/`definition`), each linked to the source page it came from. This is where cross-source convergence/divergence shows up: multiple teachers, multiple subsections.
3. `## Common mistakes` — attributions of kind mistake/correction for this entity, attributed.
4. `## Develops` (drill pages only) — from `drill_purposes`: the skills this drill trains, with `prose` and `focus_context`.
5. `## Requires` (technique pages only) — from `technique_requirements`: skills this technique needs.
6. `## Related` — from `relations` touching this entity: linked entity + `relation_kind` + prose.

### Instructor pages (`instructors/<slug>.md`)

One page per canonical instructor.

**Frontmatter:**

```yaml
---
type: instructor
slug: <slug>
canonical_name: <name>
aliases: [<from instructor.aliases>]
sources_count: <number of sources where this instructor taught>
concepts_taught: [<entity slugs with attributions by this instructor, kind=concept>]
techniques_taught: [<...>]
---
```

**Body (omit empty sections):**

1. `## Background` — render `background_md`. Omit if empty.
2. `## Teaching themes` — render `teaching_themes_md`. Omit if empty.
3. `## Notable framings` — render `notable_framings_md`. Omit if empty.
4. `## Teaching` — entities this instructor has attributions/definitions for, grouped by kind, linked.
5. `## Sources` — source pages where this instructor is a teaching authority.

There is no `## Referenced by` section — references are not linked to instructors. If the polish pass later produces instructor prose, it lands in the `*_md` columns and renders here automatically.

### Views (`views/<slug>.md`)

Pre-rendered filtered slices. Derived artifacts; never the source of a claim. Regenerated whole each run.

The v0 views:

- `views/kaianos-canon.md` — sources where Kaiano is an instructor.
- `views/kate-as-student.md` — sources where Kate is a student.
- `views/full-model.md` — the whole corpus, chronologically.

Adding a view is a renderer change (extend `REQUIRED_VIEWS` in
`src/wiki_curator_cog/render.py`). The view spec
supports `instructors_includes` and `students_includes` filters
against `sources[].instructors_raw` / `students_raw`.

Each view is a curated tour: group by entity area, link to entity and source pages, summarize what the filtered set contributes. Compute the filter from `sources[].instructors_raw` / `students_raw`.

### Index and log

`index.md` — regenerated whole each run from the rendered set. Organized by section (Concepts, Techniques, Patterns, Drills, Instructors, Sources, Views), one wikilink + one-line descriptor per page.

`log.md` — append-only. One entry per render run:

```markdown
## [YYYY-MM-DD] render | <summary>

Rendered N entities (C concepts, T techniques, P patterns, D drills), I instructors,
S sources. <Anything notable: entities with no attributions, suspected
fragmentation worth an upstream alias, empty overviews, etc.>
```

Render-run observations about data quality (fragmentation, empty prose, mis-resolved names) go in the log as feedback for upstream curation — the renderer never acts on them itself.

---

## Slug, link, and naming conventions

- Filenames are lowercase, hyphen-separated, no spaces or special characters.
- Entity and instructor slugs come straight from the canonical `slug` field — the renderer does not compute or alter them.
- Source slugs: `<session_date>-<title-slug>` when `title` is present; otherwise `<session_date>-<primary-instructor-slug>-<session_type>`.
- Internal links use `[[slug]]`. Link by alias renders the alias text but targets the canonical slug: `[[settle|drop]]`.
- Attribution granularity is loose: section-level citations (linking to the source page) are sufficient; per-claim citation is not required.

---

## Render workflow

A single flow. No modes.

1. **Authenticate** to `api-kaianolevine-com` via Clerk M2M.
2. **Clone or refresh** the `wcs-wiki` repo at `WIKI_REPO_PATH`.
3. **Fetch** `GET /v1/wcs/wiki/export` — the full corpus in one call.
4. **Build the in-memory indexes** (see "Joining the export").
5. **Render the full bundle** to disk: every entity page, every source page, every instructor page, the four views, `index.md`.
6. **Append one `log.md` entry** with the render summary and any data-quality observations.
7. **Commit and push** — one commit per run: `render: <date> (<N> entities, <S> sources)`.

The renderer overwrites the derived pages wholesale each run. There is nothing to merge, no prior state to reconcile, no per-source skip logic. If the wiki ever diverges from canonical state, a render run reconciles it.

### Concurrency

Single-instance Prefect flow (concurrency slot of 1), matching the ecosystem pattern. Two concurrent runs would race on git operations.

---

## What this wiki is not

- Not a substitute for the lesson-level notes UI. Per-lesson lookup and chronological browsing happen there.
- Not an editing surface. The render is overwritten each run; hand edits to derived pages are lost. The editable surfaces are this spec (in `wiki-curator-cog`), the wiki's `README.md`, and the canonical store (via the API's admin endpoints).
- Not authoritative. The substrate is the source of truth; the wiki is a regenerable projection.
- Not where identity or vocabulary decisions live. Those are upstream, in entity resolution and the substrate alias/merge layer.
- Not, currently, public. Built with public-readiness as a constraint (attribution discipline, descriptive voice, no unsourced opinion), but a personal tool unless Kaiano decides otherwise.

---

## Versioning

This render spec is versioned in git. Because the renderer is stateless and re-renders the full corpus every run, there is no `curator_version` to bump and no re-ingest to trigger — changing the spec and re-rendering is the entire update path. Significant spec changes get a `## [YYYY-MM-DD] schema-update` entry in `log.md`.

Current spec version: **v1.2** (patterns rendered to their own directory, separated from techniques; supersedes v1.1).