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
CONSOLIDATOR_VERSION = "1"

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
- Two names for exactly the same thing are also facets: fold the longer or \
less common name into the common one.
- The parent must be in the list of entries and must not be the candidate \
itself. When unsure, the parent is null: a wrong fold hides a page.
- "reason" is a few words a reviewer can check at a glance.
- Return every candidate id you were given. Do not invent ids.

Output ONLY valid JSON — no markdown fences, no commentary before or after — \
in exactly this shape:
{"entries": [{"id": "<candidate id>", "parent": "<entry name>" or null, \
"reason": "<a few words>"}]}
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
                "required": ["id", "parent"],
                "properties": {
                    "id": {"type": "string"},
                    "parent": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


@dataclass
class Candidate:
    """One entity, as the model is shown it."""

    slug: str
    name: str
    kind: str
    definition: str


def candidates(
    export: WcsWikiExport, definitions: dict[str, str] | None = None
) -> list[Candidate]:
    """Every foldable entity, alphabetical so related names share a batch.

    ``definitions`` (slug → text, usually the generated definitions) gives the
    model the meaning, not just the name; the export's own overview is the
    fallback.
    """
    definitions = definitions or {}
    out = [
        Candidate(
            slug=e.slug,
            name=e.canonical_name,
            kind=e.kind,
            definition=" ".join(
                (definitions.get(e.slug) or e.overview_md or "").split()
            )[:400],
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


def decide_batch(
    llm: LLMClient,
    batch: Sequence[Candidate],
    all_names: Sequence[str],
    parents: link.Catalog,
) -> dict[str, tuple[str | None, str]]:
    """One call: slug → (parent slug or None, reason).

    A parent name that matches no foldable entity, or matches the candidate
    itself, counts as standalone. Ids the model invents are ignored; ids it
    leaves out stay undecided for the next run.
    """
    result = llm.generate_json(
        messages=build_messages(batch, all_names),
        json_schema=RESPONSE_SCHEMA,
        schema_name="facets",
    )
    wanted = {c.slug for c in batch}
    out: dict[str, tuple[str | None, str]] = {}
    for item in result.output_json.get("entries", []):
        slug = str(item.get("id", ""))
        if slug not in wanted:
            continue
        raw = item.get("parent")
        parent: str | None = None
        if isinstance(raw, str) and raw.strip():
            hit = parents.find(link.api_slugify(raw))
            if hit is not None and hit.slug != slug:
                parent = hit.slug
        out[slug] = (parent, str(item.get("reason", "")).strip())
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

    def record(self, slug: str, parent: str | None, reason: str, model: str) -> None:
        self.entities[slug] = {
            "parent": parent,
            "reason": reason,
            "version": CONSOLIDATOR_VERSION,
            "model": model,
            "decided_at": dt.date.today().isoformat(),
        }

    def parents(self) -> dict[str, str]:
        """slug → parent slug, for every entity decided to be a facet."""
        return {
            slug: entry["parent"]
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
    batch_size: int = DEFAULT_BATCH_SIZE,
    workers: int = DEFAULT_WORKERS,
    on_batch: Any = None,
    sample: int | None = None,
) -> dict[str, Any]:
    """Decide every foldable entity not already decided under this version."""
    pool_of = candidates(export, definitions)
    foldable = [e for e in export.entities if e.kind in FOLDABLE_KINDS]
    all_names = sorted({e.canonical_name for e in foldable}, key=str.lower)
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
            results = decide_batch(llm, batch, all_names, parents)
        except LLMError as exc:
            with lock:
                failed_batches += 1
            LOG.warning("consolidate.batch_failed size=%s err=%s", len(batch), exc)
            return
        with lock:
            for slug, (parent, reason) in results.items():
                facets.record(slug, parent, reason, model)
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


def review_markdown(facets: FacetsFile, names: dict[str, str]) -> str:
    """Each parent with the facets folded into it, largest first."""
    groups: dict[str, list[str]] = defaultdict(list)
    for slug, parent in facets.parents().items():
        groups[parent].append(slug)
    lines = [
        f"# Facet review — version {CONSOLIDATOR_VERSION}",
        "",
        f"{sum(len(v) for v in groups.values())} facets into {len(groups)} pages. "
        "To overrule one, write a note: “X is its own concept, not part of Y” "
        "or “X is part of Z”.",
        "",
    ]
    for parent, slugs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"## {names.get(parent, parent)} ({len(slugs)})")
        lines.append("")
        for slug in sorted(slugs):
            reason = facets.entities[slug].get("reason", "")
            tail = f" — {reason}" if reason else ""
            lines.append(f"- {names.get(slug, slug)}{tail}")
        lines.append("")
    return "\n".join(lines)
