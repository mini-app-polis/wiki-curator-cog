"""Generation: an LLM maintains each entity's overview (ADR-005, MINI-86).

Local half. For each entity, the model is given the page's current overview
— whatever the page says now — and the statements not yet integrated into
it, and returns the updated overview. Newer statements win where they
conflict. The result goes to a local ``prose.json``, never to the API, and
``render --prose`` puts it on the pages.

**The page is the base.** Nobody is expected to maintain the prose:
corrections normally arrive as notes, which become dated statements like any
lesson. But if a page's overview was edited by hand, the next run starts from
the page as it stands — the edit is noticed (it matches no revision this pass
wrote), kept in the revision history, and integrated on top of.

**Two kinds of evidence.** An entity's own statements (what sources said
about it) and the statements that refer to it (from the linking pass) — the
second is what gives *connection*, which almost nothing is "about", a
substantial page. Both are capped per call, newest first; what does not fit
is integrated on the next run, so a heavily referenced concept converges
over a few runs instead of one enormous call.

**Watermarked.** Each entity records which statement keys it has integrated
under the current :data:`GENERATOR_VERSION`. An entity with nothing new costs
no call. Raising the version regenerates every page from scratch — the
rebuild path ADR-005 keeps as the escape hatch for drift.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log
from mini_app_polis.llm import LLMMessage
from mini_app_polis.llm.base import LLMClient
from mini_app_polis.llm.errors import LLMError

from . import link
from . import markdown_utils as md
from .render import (
    ExportIndexes,
    LinkOverlay,
    WcsEntity,
    WcsWikiExport,
    build_indexes,
)

LOG = log.get_logger()

#: Raised when the prompt changes in a way that should regenerate every page
#: from scratch. Entities generated under another version start over.
GENERATOR_VERSION = "4"

#: Attempts per entity before it is skipped for this run. A malformed
#: response is usually a one-off; a second failure is reported, and the
#: entity stays pending for the next run.
ATTEMPTS = 2

#: Per call: an entity's own statements, newest first.
MAX_OWN_STATEMENTS = 120
#: Per call: statements that refer to the entity, newest first.
MAX_REFERRING_STATEMENTS = 80


# ── Evidence ──


@dataclass(frozen=True)
class Evidence:
    """One statement, as the model sees it."""

    key: str
    date: str
    teacher: str
    source_slug: str
    #: "about": the statement is about this entity. "refers": it is about
    #: another entity and mentions this one; ``subject`` is that entity's
    #: wiki-link.
    role: str
    subject: str
    text: str

    def as_prompt(self) -> dict[str, str]:
        """The fields the model is shown."""
        out = {
            "id": self.key,
            "date": self.date or "undated",
            "teacher": self.teacher,
            "source": f"[[sources/{self.source_slug}]]" if self.source_slug else "",
            "role": self.role,
            "text": self.text,
        }
        if self.role == "refers":
            out["about"] = self.subject
        return out


def _instructor_ids(export: WcsWikiExport) -> dict[str, uuid.UUID | None]:
    out: dict[str, uuid.UUID | None] = {}
    for a in export.attributions:
        out[f"attribution:{a.id}"] = a.instructor_id
    for d in export.definitions:
        out[f"definition:{d.id}"] = d.instructor_id
    return out


def _teacher(
    instructor_id: uuid.UUID | None, source_id: uuid.UUID | None, indexes: ExportIndexes
) -> str:
    if instructor_id is not None:
        instructor = indexes.instructors_by_id.get(instructor_id)
        if instructor is not None:
            return instructor.canonical_name
    source = indexes.sources_by_id.get(source_id) if source_id else None
    if source is not None and source.instructors_raw:
        return ", ".join(source.instructors_raw)
    return "Unknown"


@dataclass
class Corpus:
    """Every entity's evidence, built once per run."""

    entities: dict[str, WcsEntity]
    own: dict[str, list[Evidence]] = field(default_factory=dict)
    referring: dict[str, list[Evidence]] = field(default_factory=dict)


def build_corpus(
    export: WcsWikiExport,
    overlay: LinkOverlay | None = None,
    links: link.LinksFile | None = None,
    merges: Any = None,
) -> Corpus:
    """Group every statement under the entities it is evidence for.

    ``export`` should already carry any concepts the linking pass proposed
    (see :func:`wiki_curator_cog.link.build_overlay`), so they get pages too.
    A referring statement counts only where the overlay kept its pair — a
    reference a later statement denied is not evidence.
    """
    indexes = build_indexes(export)
    by_slug = {e.slug: e for e in export.entities}
    corpus_entities = dict(by_slug)
    if merges:
        for old_slug, new_slug in merges.by_slug.items():
            if new_slug in by_slug:
                by_slug[old_slug] = by_slug[new_slug]
    instructor_ids = _instructor_ids(export)
    statements = link.collect_statements(export)
    by_key = {s.key: s for s in statements}

    def evidence(s: link.Statement, role: str) -> Evidence:
        return Evidence(
            key=s.key,
            date=s.session_date.isoformat() if s.session_date else "",
            teacher=_teacher(instructor_ids.get(s.key), s.source_id, indexes),
            source_slug=indexes.source_slug_by_id.get(s.source_id, "")
            if s.source_id
            else "",
            role=role,
            subject=_link(by_slug.get(s.subject_slug)),
            text=s.text,
        )

    corpus = Corpus(entities=corpus_entities)
    for s in statements:
        corpus.own.setdefault(s.subject_slug, []).append(evidence(s, "about"))

    if overlay is not None and links is not None:
        for key, entry in links.statements.items():
            statement = by_key.get(key)
            if statement is None:
                continue
            subject = by_slug.get(statement.subject_slug)
            if subject is None:
                continue
            for r in entry.get("references", []):
                if r.get("polarity") == "deny":
                    continue
                target = by_slug.get(r["target_slug"])
                if target is None or target.id == subject.id:
                    continue
                if subject.id not in overlay.referenced_by.get(target.id, {}):
                    continue
                corpus.referring.setdefault(target.slug, []).append(
                    evidence(statement, "refers")
                )
    return corpus


def _link(entity: WcsEntity | None) -> str:
    if entity is None:
        return ""
    from .render import _entity_path  # the renderer owns paths

    return f"[[{_entity_path(entity)[:-3]}]]"


def _newest(items: Iterable[Evidence], limit: int) -> list[Evidence]:
    return sorted(items, key=lambda e: (e.date, e.key), reverse=True)[:limit]


# ── The prose file (and watermark) ──


@dataclass
class ProseFile:
    """Every entity's generated overview, its history and its watermark."""

    generator_version: str = GENERATOR_VERSION
    model: str = ""
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ProseFile:
        """The file at ``path``; an empty one if it does not exist."""
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            generator_version=str(raw.get("generator_version", "")),
            model=str(raw.get("model", "")),
            entities=dict(raw.get("entities", {})),
        )

    def save(self, path: Path) -> None:
        """Write sorted, so an unchanged file keeps its bytes."""
        payload = {
            "generator_version": self.generator_version,
            "model": self.model,
            "entities": dict(sorted(self.entities.items())),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def current(self, slug: str) -> dict[str, Any] | None:
        """The entry for ``slug`` under this generator version, if any."""
        entry = self.entities.get(slug)
        if entry is None or entry.get("generator_version") != GENERATOR_VERSION:
            return None
        return entry

    def definition(self, slug: str) -> str:
        """The latest definition for ``slug`` under this version; "" if none."""
        entry = self.current(slug)
        return entry.get("definition", "") if entry else ""

    def context(self, slug: str) -> str:
        """The latest context for ``slug`` under this version; "" if none."""
        entry = self.current(slug)
        return entry.get("context", "") if entry else ""

    def integrated(self, slug: str) -> set[str]:
        """Statement keys already folded into ``slug``'s overview."""
        entry = self.current(slug)
        return set(entry["integrated"]) if entry else set()

    def written_by_us(self, slug: str, definition: str, context: str) -> bool:
        """Whether this pair is any definition and context this pass wrote."""
        entry = self.entities.get(slug)
        if entry is None:
            return False
        return any(
            r["by"] == "llm"
            and r.get("definition", r.get("prose", "")).strip() == definition.strip()
            and r.get("context", "").strip() == context.strip()
            for r in entry.get("revisions", [])
        )


#: The page sections generation owns: the definition comes first on the page,
#: the context further down.
DEFINITION_SECTION = "Overview"
CONTEXT_SECTION = "In practice"


def page_prose(wiki_repo_path: Path | None, entity: WcsEntity) -> tuple[str, str]:
    """The definition and context the entity's page shows now; "" where absent."""
    if wiki_repo_path is None:
        return "", ""
    from .render import _entity_path  # the renderer owns paths

    path = wiki_repo_path / _entity_path(entity)
    if not path.exists():
        return "", ""
    page = md.parse(path.read_text())

    def section(name: str) -> str:
        body = page.get_section(name)
        return "\n".join(body).strip() if body else ""

    return section(DEFINITION_SECTION), section(CONTEXT_SECTION)


def base_for(
    entity: WcsEntity, prose: ProseFile, wiki_repo_path: Path | None, today: str
) -> tuple[str, str]:
    """The definition and context to build on: the page as it stands.

    If the page shows prose this pass never wrote, someone edited it. The
    edit is recorded as a revision and becomes the base. Otherwise the
    latest generated prose is the base — the page may simply not have been
    rendered since.
    """
    definition, context = page_prose(wiki_repo_path, entity)
    if (definition or context) and not prose.written_by_us(
        entity.slug, definition, context
    ):
        entry = prose.entities.setdefault(
            entity.slug,
            {
                "entity_id": str(entity.id),
                "generator_version": GENERATOR_VERSION,
                "definition": "",
                "context": "",
                "integrated": [],
                "revisions": [],
            },
        )
        entry.setdefault("revisions", []).append(
            {"by": "page", "at": today, "definition": definition, "context": context}
        )
        return definition, context
    return prose.definition(entity.slug), prose.context(entity.slug)


# ── The model ──

SYSTEM_PROMPT = """\
You write two short pieces for one page of a West Coast Swing (WCS) wiki: \
its definition and its context. Further down, the page already lists every \
teacher's detailed notes, mistakes and related pages, so neither piece \
repeats them.

definition — the first section of the page. A simple definition, like a \
good glossary entry: one or two plain sentences, at most about 40 words, \
saying what it is. No citations, no links, no history, no advice, no \
qualifications.

context — optional, shown lower on the page under "In practice". Only the \
few points a dancer most needs beyond the definition: how it works or is \
done, the key thing people get wrong and what is right instead, how it \
connects to the bigger ideas. One paragraph, at most about 80 words. Leave \
it as an empty string when there is nothing worth adding — common for thin \
entities.

You are given the current definition and context (possibly empty) and new \
statements from lessons and notes. Return the updated pair.

Voice: an experienced dancer explaining to another dancer. Synthesise in \
your own words; do not paraphrase lesson by lesson or recount who said what. \
Short sentences, everyday language, no hedging, no filler. Shorter is better.

Rules:
- Keep what the current text says unless new statements contradict it or \
add something that matters; you are revising, not starting over. Most new \
statements will not change a good definition.
- Newer statements win. If a newer one contradicts an older idea, state the \
newer idea; mention the change only if it is itself worth knowing.
- Name a teacher only when teachers genuinely disagree.
- Statements with role "refers" are about another entity and mention this \
one; use them to understand how this idea shows up in the dance. In the \
context only, link at most two of the most important, exactly as given in \
"about" (e.g. [[techniques/anchor-step]]).
- In the context only, cite sparingly: at most three citations, each the \
exact source link given, e.g. ([[sources/2026-03-01-anchor-step]]). Never \
invent a source, fact or quote.
- No headings, no bullet lists, no preamble. Never end with "See also" or \
any list of related pages — the page lists those itself. Do not mention \
statements, sources, lessons or this process.

Output ONLY valid JSON — no markdown fences, no commentary before or after — \
in exactly this shape:
{"definition": "<one or two sentences>", "context": "<short markdown or empty>"}
Inside the text never use the straight double-quote character ("); use \
curly quotes (“ ”) or single quotes instead, so the JSON stays valid.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["definition", "context"],
    "properties": {
        "definition": {"type": "string"},
        "context": {"type": "string"},
    },
}


def build_messages(
    entity: WcsEntity, base: tuple[str, str], evidence: Sequence[Evidence]
) -> list[LLMMessage]:
    """The entity, its current definition and context, and the new evidence."""
    user = "\n".join(
        [
            f"Entity: {entity.canonical_name} (kind: {entity.kind}, slug: {entity.slug})",
            "",
            "Current definition:",
            base[0].strip() or "(empty)",
            "",
            "Current context:",
            base[1].strip() or "(empty)",
            "",
            "New statements (JSON, newest first):",
            json.dumps([e.as_prompt() for e in evidence], ensure_ascii=False, indent=1),
        ]
    )
    return [
        LLMMessage(role="system", content=SYSTEM_PROMPT),
        LLMMessage(role="user", content=user),
    ]


# ── A run ──


def pending(entity: WcsEntity, corpus: Corpus, prose: ProseFile) -> list[Evidence]:
    """This entity's evidence not yet integrated, capped, newest first."""
    done = prose.integrated(entity.slug)
    own = [e for e in corpus.own.get(entity.slug, []) if e.key not in done]
    refers = [e for e in corpus.referring.get(entity.slug, []) if e.key not in done]
    return _newest(own, MAX_OWN_STATEMENTS) + _newest(refers, MAX_REFERRING_STATEMENTS)


def choose_entities(
    corpus: Corpus,
    prose: ProseFile,
    *,
    slugs: Sequence[str] = (),
    sample: int | None = None,
    seed: int = 0,
) -> list[WcsEntity]:
    """Which entities this run generates.

    Named ``slugs`` if given; otherwise every entity with pending evidence,
    or a reproducible sample of them spread across kinds.
    """
    if slugs:
        missing = [s for s in slugs if s not in corpus.entities]
        if missing:
            raise RuntimeError(f"unknown entity slugs: {', '.join(missing)}")
        return [corpus.entities[s] for s in slugs]
    candidates = [
        e
        for e in sorted(corpus.entities.values(), key=lambda e: e.slug)
        if pending(e, corpus, prose)
    ]
    if sample is None:
        return candidates
    rng = random.Random(seed)
    by_kind: dict[str, list[WcsEntity]] = {}
    for e in candidates:
        by_kind.setdefault(e.kind, []).append(e)
    for bucket in by_kind.values():
        rng.shuffle(bucket)
    picked: list[WcsEntity] = []
    while len(picked) < sample and any(by_kind.values()):
        for kind in sorted(by_kind):
            if by_kind[kind] and len(picked) < sample:
                picked.append(by_kind[kind].pop())
    return picked


def generate_one(
    llm: LLMClient,
    entity: WcsEntity,
    corpus: Corpus,
    prose: ProseFile,
    *,
    model: str,
    wiki_repo_path: Path | None,
    today: str,
) -> bool:
    """Integrate this entity's pending evidence. False if there was none."""
    evidence = pending(entity, corpus, prose)
    if not evidence:
        return False
    base = base_for(entity, prose, wiki_repo_path, today)
    result = llm.generate_json(
        messages=build_messages(entity, base, evidence),
        json_schema=RESPONSE_SCHEMA,
        schema_name="prose",
    )
    definition = str(result.output_json.get("definition", "")).strip()
    context = str(result.output_json.get("context", "")).strip()
    entry = prose.entities.get(entity.slug)
    if entry is None or entry.get("generator_version") != GENERATOR_VERSION:
        history = entry.get("revisions", []) if entry else []
        entry = {"revisions": history, "integrated": []}
        prose.entities[entity.slug] = entry
    entry.update(
        {
            "entity_id": str(entity.id),
            "generator_version": GENERATOR_VERSION,
            "model": model,
            "generated_at": today,
            "definition": definition,
            "context": context,
            "integrated": sorted(
                set(entry.get("integrated", [])) | {e.key for e in evidence}
            ),
        }
    )
    entry.setdefault("revisions", []).append(
        {
            "by": "llm",
            "at": today,
            "model": model,
            "statements": len(evidence),
            "definition": definition,
            "context": context,
        }
    )
    return True


def run_generation(
    entities: Sequence[WcsEntity],
    corpus: Corpus,
    prose: ProseFile,
    llm: LLMClient,
    *,
    model: str,
    wiki_repo_path: Path | None = None,
    today: str | None = None,
    on_entity: Any = None,
) -> dict[str, Any]:
    """Generate each entity in turn; ``on_entity`` runs after each, to save.

    One entity's bad response does not end the run: it is retried, then
    skipped and named in ``failed``, and stays pending — its statements are
    not marked integrated — so the next run tries it again.
    """
    day = today or dt.date.today().isoformat()
    generated = 0
    failed: list[str] = []
    for i, entity in enumerate(entities, 1):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                if generate_one(
                    llm,
                    entity,
                    corpus,
                    prose,
                    model=model,
                    wiki_repo_path=wiki_repo_path,
                    today=day,
                ):
                    generated += 1
                break
            except LLMError as exc:
                LOG.warning(
                    "generate.entity_failed slug=%s attempt=%s/%s err=%s",
                    entity.slug,
                    attempt,
                    ATTEMPTS,
                    exc,
                )
                if attempt == ATTEMPTS:
                    failed.append(entity.slug)
        if on_entity is not None:
            on_entity()
        LOG.info("generate.entity done=%s/%s slug=%s", i, len(entities), entity.slug)
    prose.generator_version = GENERATOR_VERSION
    prose.model = model
    return {
        "entities": len(entities),
        "generated": generated,
        "failed": len(failed),
        "failed_slugs": failed,
    }


def apply_prose(export: WcsWikiExport, prose: ProseFile) -> WcsWikiExport:
    """The export with each entity's generated definition in ``overview_md``."""
    entities = []
    for e in export.entities:
        text = prose.definition(e.slug)
        entities.append(e.model_copy(update={"overview_md": text}) if text else e)
    return export.model_copy(update={"entities": entities})


def context_by_entity(export: WcsWikiExport, prose: ProseFile) -> dict[uuid.UUID, str]:
    """Each entity's generated context, for the page's ``## In practice``."""
    out: dict[uuid.UUID, str] = {}
    for e in export.entities:
        text = prose.context(e.slug)
        if text:
            out[e.id] = text
    return out


def review_markdown(prose: ProseFile, slugs: Iterable[str]) -> str:
    """Each generated overview, for reading before rendering."""
    lines = [f"# Generation review — version {GENERATOR_VERSION}", ""]
    for slug in slugs:
        entry = prose.current(slug)
        if entry is None:
            continue
        last = entry["revisions"][-1] if entry.get("revisions") else {}
        lines += [
            f"## {slug}",
            "",
            f"_{last.get('statements', '?')} statements integrated this run; "
            f"{len(entry['integrated'])} in total._",
            "",
            f"**Definition.** {entry.get('definition') or '_(empty)_'}",
            "",
            f"**In practice.** {entry.get('context') or '_(none)_'}",
            "",
        ]
    return "\n".join(lines)
