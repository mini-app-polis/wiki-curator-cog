"""Facet consolidation: narrow entities folded into the concept they detail (MINI-93).

Extraction mints a new entity for every phrasing a lesson uses — "anchor",
"anchor with directional intent", "anchoring to reset hand connection" — so
one idea ends up spread over dozens of thin pages. These are not aliases (two
names, one thing) but facets: narrower cases, variations, caveats or details
of one broader concept.

This pass asks the model, for each concept and technique, whether it is a
facet of another, broader one already in the corpus, and records the answer
in a local JSON file with a version watermark, like the linker's. Nothing in
the API changes. :mod:`wiki_curator_cog.identity` folds the facets in when the
wiki is built: the facet's page goes, its statements and references move to
the broader concept, and the broader page lists it under ``## Details``.

Notes outrank this pass: a note's ``facet_of``, ``alias_of`` or
``distinct_from`` for the same pair or entity wins over the model's call, so
"X is its own concept" in a note splits it back out on the next build.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log
from mini_app_polis.llm import LLMMessage
from mini_app_polis.llm.base import LLMClient
from mini_app_polis.llm.errors import LLMError

from . import link
from .identity import FOLDABLE_KINDS
from .render import WcsWikiExport

LOG = log.get_logger()

#: Bump when the prompt or the rules change in a way that should redecide
#: every entity; entities decided under an older version are asked again.
CONSOLIDATOR_VERSION = "4"

DEFAULT_BATCH_SIZE = 50
DEFAULT_WORKERS = 8


SYSTEM_PROMPT = """\
You tidy a wiki of West Coast Swing (WCS) concepts built automatically from \
lesson notes. Extraction created a separate entry for every phrasing, so one \
idea is often spread over many narrow entries: "anchor", "anchor with \
directional intent", "anchoring to reset hand connection", "stationary \
anchor" are all anchor, with caveats, variations or details.

For each candidate entry, decide whether it is a FACET of a broader entry \
from the list of all entries: a narrower case, variation, quality, caveat, \
purpose or detail of that broader idea, which a reader would expect to find \
as a detail on the broader page. If so, give the broader entry's name exactly \
as listed. If not, the parent is null and the entry keeps its own page.

Rules:
- Choose the most specific broader entry that is still a real, established \
concept, usually short: "stationary anchor cue" is a facet of "anchor", and \
"anchor step foot position" a facet of "anchor step" if both exist.
- A facet is about the same idea. Relating to an idea is not enough: a \
technique that uses the anchor, a pattern that ends in one, or a principle \
that mentions connection is not a facet of anchor or of connection.
- Do not fold into the most general concepts (connection, leading, \
following, frame, timing, musicality) just because the entry is broadly about \
them. "Connection" absorbs "connection quality", not every idea a partner \
feels through the hand.
- Comparisons of two ideas ("middle triple vs anchor triple") and entries \
that name their own distinct move, figure or principle stay standalone.
- "same" is true when the candidate says nothing the parent does not: \
another name or phrasing for exactly the same idea ("anchor as zero \
velocity" when the anchor is defined as a deceleration to zero). It is then \
folded in as another name, with no line of its own. A "framing", \
"perspective" or "way of thinking about" the parent that restates what the \
parent is ("anchor as a stationary moment", "anchor as stop and redirect", \
"anchor purpose" when the anchor is defined as a stop then a redirect) is \
the same idea: "same" is true. "same" is false only when the candidate adds \
something a reader of the parent's definition would not already know: a \
variation, a condition, a mistake, a mechanism or a specific use.
- Support is evidence. Every entry carries how many sources teach it and \
how many statements elsewhere refer to it; every name in the list of entries \
carries its total in brackets. An entry with one source and almost no \
references is probably a note on another topic rather than a concept of its \
own: fold it into the entry it is about whenever there is a plausible one. A \
well-supported entry is established: fold it only when it is clearly a facet \
or the same idea. Prefer well-supported parents.
- The parent must be in the list of entries, without its bracketed count, \
and must not be the candidate itself. A parent with less support than the \
candidate is refused. When unsure about a well-supported entry, the parent is null: a wrong fold \
hides a page.
- "reason" is a few words a reviewer can check at a glance.
- Return every candidate id you were given. Do not invent ids.

Output ONLY valid JSON — no markdown fences, no commentary before or after — \
in exactly this shape:
{"entries": [{"id": "<candidate id>", "parent": "<entry name>" or null, \
"same": false, "reason": "<a few words>"}]}
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": ["entries"],
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                # Only the id is required, and nothing else is typed: a stray
                # null in one entry must not cost the other 49 their answers.
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        }
    },
}


@dataclass(frozen=True)
class Support:
    """How established an entity is: the evidence behind it.

    ``sources`` is how many distinct sources teach it (its own statements);
    ``references`` how many statements about other entities refer to it,
    after the linker's newest-wins settling. An entity named once in one
    lesson and nowhere else is weakly supported — maybe wrong, maybe not a
    concept at all but a note on another topic.
    """

    sources: int = 0
    references: int = 0

    @property
    def weight(self) -> int:
        return self.sources + self.references

    @property
    def thin(self) -> bool:
        """One source at most, and at most one reference from elsewhere."""
        return self.sources <= 1 and self.references <= 1


def support(
    export: WcsWikiExport, links: link.LinksFile | None = None, merges: Any = None
) -> dict[str, Support]:
    """Every entity's support, by slug.

    ``export`` is the merged export; ``merges`` redirects references to
    merged-away names, as at build time.
    """
    sources: dict[Any, set[Any]] = defaultdict(set)
    for a in export.attributions:
        sources[a.entity_id].add(a.source_id)
    for d in export.definitions:
        sources[d.entity_id].add(d.source_id)
    for p in export.drill_purposes:
        sources[p.drill_entity_id].add(p.source_id)
    for r in export.technique_requirements:
        sources[r.technique_entity_id].add(r.source_id)

    references: dict[Any, int] = {}
    if links is not None:
        _, overlay, _ = link.build_overlay(export, links, merges)
        references = {
            target: sum(by_subject.values())
            for target, by_subject in overlay.referenced_by.items()
        }
    return {
        e.slug: Support(
            sources=len(sources.get(e.id, ())), references=references.get(e.id, 0)
        )
        for e in export.entities
    }


@dataclass
class Candidate:
    """One entity, as the model is shown it."""

    slug: str
    name: str
    kind: str
    definition: str
    support: Support = field(default_factory=Support)


def candidates(
    export: WcsWikiExport,
    definitions: dict[str, str] | None = None,
    supports: dict[str, Support] | None = None,
) -> list[Candidate]:
    """Every foldable entity, alphabetical so related names share a batch.

    ``definitions`` (slug → text, usually the generated definitions) gives the
    model the meaning, not just the name; the export's own overview is the
    fallback.
    """
    definitions = definitions or {}
    supports = supports or {}
    out = [
        Candidate(
            slug=e.slug,
            name=e.canonical_name,
            kind=e.kind,
            definition=" ".join(
                (definitions.get(e.slug) or e.overview_md or "").split()
            )[:400],
            support=supports.get(e.slug, Support()),
        )
        for e in export.entities
        if e.kind in FOLDABLE_KINDS
    ]
    return sorted(out, key=lambda c: c.slug)


def build_messages(
    batch: Sequence[Candidate], all_names: Sequence[str]
) -> list[LLMMessage]:
    """The full list of entries (the same for every call), then the batch."""
    user = "\n".join(
        [
            "All entries (possible parents):",
            "; ".join(all_names),
            "",
            "Candidates (JSON):",
            json.dumps(
                [
                    {
                        "id": c.slug,
                        "name": c.name,
                        "kind": c.kind,
                        "definition": c.definition,
                        "sources": c.support.sources,
                        "referenced_by": c.support.references,
                    }
                    for c in batch
                ],
                ensure_ascii=False,
                indent=1,
            ),
        ]
    )
    return [
        LLMMessage(role="system", content=SYSTEM_PROMPT),
        LLMMessage(role="user", content=user),
    ]


#: The bracketed support count the list of entries carries after each name.
_COUNT = re.compile(r"\s*\[\d+\]\s*$")


def decide_batch(
    llm: LLMClient,
    batch: Sequence[Candidate],
    all_names: Sequence[str],
    parents: link.Catalog,
    supports: dict[str, Support] | None = None,
) -> dict[str, tuple[str | None, bool, str]]:
    """One call: slug → (parent slug or None, same as the parent, reason).

    A parent name that matches no foldable entity, or matches the candidate
    itself, counts as standalone. So does a parent with less support than
    the candidate: an established entity is never folded into a thinner one. Ids the model invents are ignored; ids it
    leaves out stay undecided for the next run.
    """
    result = llm.generate_json(
        messages=build_messages(batch, all_names),
        json_schema=RESPONSE_SCHEMA,
        schema_name="facets",
    )
    supports = supports or {}
    by_slug = {c.slug: c for c in batch}
    out: dict[str, tuple[str | None, bool, str]] = {}
    for item in result.output_json.get("entries", []):
        slug = str(item.get("id", ""))
        candidate = by_slug.get(slug)
        if candidate is None:
            continue
        raw = item.get("parent")
        reason = str(item.get("reason") or "").strip()
        parent: str | None = None
        if isinstance(raw, str) and raw.strip():
            hit = parents.find(link.api_slugify(_COUNT.sub("", raw)))
            if hit is not None and hit.slug != slug:
                parent = hit.slug
        if parent is not None:
            weight = supports.get(parent, Support()).weight
            if weight < candidate.support.weight:
                reason = f"refused {parent}: less support than this entry"
                parent = None
        same = parent is not None and item.get("same") is True
        out[slug] = (parent, same, reason)
    return out


# ── The facets file (and watermark) ──


@dataclass
class FacetsFile:
    """Every entity decided so far: its parent, or None for standalone."""

    consolidator_version: str = CONSOLIDATOR_VERSION
    model: str = ""
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> FacetsFile:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            consolidator_version=raw.get("consolidator_version", CONSOLIDATOR_VERSION),
            model=raw.get("model", ""),
            entities=raw.get("entities", {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "consolidator_version": self.consolidator_version,
            "model": self.model,
            "entities": dict(sorted(self.entities.items())),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")

    def is_decided(self, slug: str) -> bool:
        entry = self.entities.get(slug)
        return entry is not None and entry.get("version") == CONSOLIDATOR_VERSION

    def record(
        self, slug: str, parent: str | None, reason: str, model: str, same: bool = False
    ) -> None:
        self.entities[slug] = {
            "parent": parent,
            "same": same,
            "reason": reason,
            "version": CONSOLIDATOR_VERSION,
            "model": model,
            "decided_at": dt.date.today().isoformat(),
        }

    def parents(self) -> dict[str, str]:
        """slug → parent slug, for every entity decided to fold."""
        return {
            slug: entry["parent"]
            for slug, entry in self.entities.items()
            if entry.get("parent")
        }

    def folds(self) -> dict[str, tuple[str, bool]]:
        """slug → (parent slug, listed as a facet), for every entity to fold.

        An entity the model judged the same as its parent folds as another
        name: no line under ``## Details``.
        """
        return {
            slug: (entry["parent"], not entry.get("same", False))
            for slug, entry in self.entities.items()
            if entry.get("parent")
        }


def run_consolidation(
    export: WcsWikiExport,
    facets: FacetsFile,
    llm: LLMClient,
    *,
    model: str,
    definitions: dict[str, str] | None = None,
    supports: dict[str, Support] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    workers: int = DEFAULT_WORKERS,
    on_batch: Any = None,
    sample: int | None = None,
) -> dict[str, Any]:
    """Decide every foldable entity not already decided under this version."""
    supports = supports if supports is not None else support(export)
    pool_of = candidates(export, definitions, supports)
    foldable = [e for e in export.entities if e.kind in FOLDABLE_KINDS]
    all_names = sorted(
        {
            f"{e.canonical_name} [{supports.get(e.slug, Support()).weight}]"
            for e in foldable
        },
        key=str.lower,
    )
    parents = link.Catalog(foldable)

    todo = [c for c in pool_of if not facets.is_decided(c.slug)]
    skipped = len(pool_of) - len(todo)
    if sample is not None:
        todo = todo[:sample]
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    lock = threading.Lock()
    decided = 0
    failed_batches = 0

    def work(batch: list[Candidate]) -> None:
        nonlocal decided, failed_batches
        try:
            results = decide_batch(llm, batch, all_names, parents, supports)
        except LLMError as exc:
            with lock:
                failed_batches += 1
            LOG.warning("consolidate.batch_failed size=%s err=%s", len(batch), exc)
            return
        with lock:
            for slug, (parent, same, reason) in results.items():
                facets.record(slug, parent, reason, model, same)
                decided += 1
            if on_batch is not None:
                on_batch()
            LOG.info("consolidate.batch decided_total=%s/%s", decided, len(todo))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, batches))

    facets.consolidator_version = CONSOLIDATOR_VERSION
    facets.model = model
    return {
        "candidates": len(pool_of),
        "skipped": skipped,
        "decided": decided,
        "failed_batches": failed_batches,
        "facets": len(facets.parents()),
    }


def review_markdown(
    facets: FacetsFile,
    names: dict[str, str],
    supports: dict[str, Support] | None = None,
) -> str:
    """Each page with everything folded into it, largest first.

    Chains are followed to the page the fold ends on (cycles are left out,
    as the build leaves them standing), so the review matches the wiki.
    Entities folded as another name are marked. With ``supports``, each
    line shows its evidence (sources / references), and thin entities that
    found no home are listed at the end: candidates for a note.
    """
    supports = supports or {}

    def ev(slug: str) -> str:
        s = supports.get(slug)
        return f" `{s.sources}s/{s.references}r`" if s is not None else ""

    parents = facets.parents()
    groups: dict[str, list[str]] = defaultdict(list)
    for slug in parents:
        seen = {slug}
        end = parents[slug]
        while end in parents and end not in seen:
            seen.add(end)
            end = parents[end]
        if end in seen:
            continue
        groups[end].append(slug)
    lines = [
        f"# Facet review — version {CONSOLIDATOR_VERSION}",
        "",
        f"{sum(len(v) for v in groups.values())} facets into {len(groups)} pages. "
        "To overrule one, write a note: “X is its own concept, not part of Y” "
        "or “X is part of Z”.",
        "",
    ]
    for parent, slugs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"## {names.get(parent, parent)}{ev(parent)} ({len(slugs)})")
        lines.append("")
        for slug in sorted(slugs):
            entry = facets.entities[slug]
            reason = entry.get("reason", "")
            tail = f" — {reason}" if reason else ""
            via = (
                f" (via {names.get(entry['parent'], entry['parent'])})"
                if entry["parent"] != parent
                else ""
            )
            mark = " _(same; another name)_" if entry.get("same") else ""
            lines.append(f"- {names.get(slug, slug)}{ev(slug)}{via}{mark}{tail}")
        lines.append("")

    homeless = sorted(
        slug
        for slug, entry in facets.entities.items()
        if not entry.get("parent") and slug in supports and supports[slug].thin
    )
    if homeless:
        lines += [
            f"## Thin and standalone ({len(homeless)})",
            "",
            "One source at most and almost no references, but no broader page "
            "was found for them. Each may be a note on another topic.",
            "",
        ]
        lines += [f"- {names.get(slug, slug)}{ev(slug)}" for slug in homeless]
        lines.append("")
    return "\n".join(lines)
