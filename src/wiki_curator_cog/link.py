"""The linking pass: the concepts each statement mentions become references.

ADR-0005 (local half). The extraction names specific ideas — "anchor
as a stationary arrival" — and rarely the general concept they belong to, so
*connection*, *anchor* and *timing* are discussed on hundreds of pages and
linked from almost none. This pass reads each statement and names the
concepts it refers to, general ones included, so that the wiki becomes a web.

A **statement** is one thing a source said about one entity: an attribution,
a definition, a drill purpose or a technique requirement. Its **subject** is
that entity. A **reference** points from the subject to a concept the
statement mentions, and carries the statement's source and date, so that
"newest wins" can be applied per pair later. A reference can **deny** as well
as assert ("frame has nothing to do with anchor").

This half is local: references are written to a JSON file, never to the API,
so the prompt can be iterated on against the real corpus without writing to
any database. The file doubles as the watermark — a statement already linked
under the current :data:`LINKER_VERSION` is skipped, which is the per-statement
watermark the API will hold once the pass is made durable.

Targets resolve the way the API's composition does (``slugify``, aliases,
``depluralize``), mirrored here so that a concept this pass proposes gets the
slug the API would give it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from mini_app_polis import logger as log
from mini_app_polis.llm import LLMMessage, build_llm
from mini_app_polis.llm.base import LLMClient
from mini_app_polis.llm.errors import LLMError

from ._parallel import run_parallel
from .render import LinkOverlay, WcsEntity, WcsWikiExport

LOG = log.get_logger()

#: Raised whenever the prompt, schema or resolution rules change in a way that
#: should relink every statement. Statements linked under another version are
#: linked again.
LINKER_VERSION = "3"

#: Statements per LLM call. Statements are short, so the fixed instructions
#: dominate a small batch; 50 keeps a call cheap to retry and the run to about
#: a hundred calls for the corpus as it stands.
DEFAULT_BATCH_SIZE = 50

#: Same provider default and override variables as transcription-cog.
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}

#: The catalog makes each prompt large; give a call room to finish.
_LLM_TIMEOUT_SECONDS = 180.0
_LLM_MAX_RETRIES = 2


def llm_settings() -> tuple[str, str]:
    """The provider and model in effect, defaults applied; nothing is checked.

    Empty means the default: an optional setting is held empty in Doppler
    until it is set, and `doppler run` passes it through as "". An unknown
    provider comes back as given, with no default model;
    :func:`llm_from_env` refuses it.
    """
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or "anthropic"
    model = (os.getenv("LLM_MODEL") or "").strip() or _DEFAULT_MODELS.get(provider, "")
    return provider, model


def llm_from_env(*, dry_run: bool = False) -> tuple[LLMClient, str]:
    """The client and model name, from ``LLM_PROVIDER`` / ``LLM_MODEL``.

    The client is metered (:mod:`wiki_curator_cog.usage`): every command's
    summary reports its calls, tokens and estimated cost. With ``dry_run``
    no client is built and no API key is needed: calls are counted and
    measured, never sent.
    """
    from . import usage

    provider, model = llm_settings()
    if provider not in _DEFAULT_MODELS:
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider!r}. Must be one of {sorted(_DEFAULT_MODELS)}."
        )
    name = f"{provider}/{model}"
    if dry_run:
        return usage.DryRunLLM(name), name
    client = build_llm(
        provider=provider,
        model=model,
        timeout_s=_LLM_TIMEOUT_SECONDS,
        max_retries=_LLM_MAX_RETRIES,
    )
    return usage.MeteredLLM(client, name), name


Polarity = Literal["assert", "deny"]
StatementKind = Literal[
    "attribution", "definition", "drill_purpose", "technique_requirement"
]


# ── API-compatible slugs ──


def api_slugify(name: str) -> str:
    """The API's ``slugify``: lowercase, hyphenate, ASCII-only, no apostrophes."""
    s = name.strip().lower()
    s = s.replace("'", "").replace("’", "")
    s = s.replace("&", " ")
    s = re.sub(r"[^a-z0-9\-]+", " ", s)
    s = re.sub(r"[\s\-]+", "-", s.strip())
    return s.strip("-")


def api_depluralize(slug: str) -> str:
    """The API's ``depluralize``, for matching ``anchor-steps`` to ``anchor-step``."""
    if slug.endswith("ies") and len(slug) > 4:
        return slug[:-3] + "y"
    if slug.endswith("es") and len(slug) > 3:
        return slug[:-2]
    if slug.endswith("s") and not slug.endswith("ss") and "-" in slug and len(slug) > 2:
        return slug[:-1]
    return slug


# ── Statements ──


@dataclass(frozen=True)
class Statement:
    """One thing one source said about one entity."""

    kind: StatementKind
    id: uuid.UUID
    subject_id: uuid.UUID
    subject_slug: str
    source_id: uuid.UUID | None
    session_date: dt.date | None
    text: str

    @property
    def key(self) -> str:
        """Stable across runs; the watermark is keyed on it."""
        return f"{self.kind}:{self.id}"


def _join(*parts: str | None) -> str:
    return " — ".join(p.strip() for p in parts if p and p.strip())


def collect_statements(export: WcsWikiExport) -> list[Statement]:
    """Every statement in the export whose subject is a known entity.

    A statement with no text, or whose subject is not in the export, is
    skipped: there is nothing to read, or nothing to point from.
    """
    entities = {e.id: e for e in export.entities}
    dates = {s.id: s.session_date for s in export.sources}
    out: list[Statement] = []

    def add(
        kind: StatementKind,
        sid: uuid.UUID,
        subject: uuid.UUID,
        source_id: uuid.UUID | None,
        text: str,
    ) -> None:
        entity = entities.get(subject)
        if entity is None or not text:
            return
        out.append(
            Statement(
                kind=kind,
                id=sid,
                subject_id=subject,
                subject_slug=entity.slug,
                source_id=source_id,
                session_date=dates.get(source_id) if source_id else None,
                text=text,
            )
        )

    for a in export.attributions:
        text = _join(
            a.raw_term,
            a.prose,
            a.drill_goal,
            "; ".join(a.drill_steps or []),
            a.mistake_text,
            a.correction_text,
        )
        add("attribution", a.id, a.entity_id, a.source_id, text)
    for d in export.definitions:
        add("definition", d.id, d.entity_id, d.source_id, _join(d.term, d.definition))
    for p in export.drill_purposes:
        add(
            "drill_purpose",
            p.id,
            p.drill_entity_id,
            p.source_id,
            _join(p.skill_name, p.prose, p.focus_context),
        )
    for r in export.technique_requirements:
        add(
            "technique_requirement",
            r.id,
            r.technique_entity_id,
            r.source_id,
            _join(r.skill_name, r.prose),
        )
    return out


def sample_statements(
    statements: Sequence[Statement], n: int, *, seed: int = 0
) -> list[Statement]:
    """A reproducible sample of ``n``, spread across statement kinds.

    Round-robin over kinds so a sample of 20 is not 20 attributions just
    because attributions are the majority; seeded so the same sample comes
    back on a re-run and the watermark can be seen to skip it.
    """
    rng = random.Random(seed)
    by_kind: dict[str, list[Statement]] = {}
    for s in sorted(statements, key=lambda s: s.key):
        by_kind.setdefault(s.kind, []).append(s)
    for bucket in by_kind.values():
        rng.shuffle(bucket)
    picked: list[Statement] = []
    kinds = sorted(by_kind)
    while len(picked) < n and any(by_kind[k] for k in kinds):
        for k in kinds:
            if by_kind[k] and len(picked) < n:
                picked.append(by_kind[k].pop())
    return picked


# ── Resolution ──


@dataclass(frozen=True)
class Reference:
    """One concept a statement mentions, resolved against the catalog."""

    name: str
    target_slug: str
    #: None when no entity has this slug yet: the pass proposes a new concept.
    target_entity_id: str | None
    polarity: Polarity

    @property
    def new(self) -> bool:
        """Whether the target is a concept the corpus does not have yet."""
        return self.target_entity_id is None


class Catalog:
    """The export's entities, looked up the way the API resolves a name."""

    def __init__(self, entities: Iterable[WcsEntity]) -> None:
        self._by_slug: dict[str, WcsEntity] = {}
        self._by_alias: dict[str, WcsEntity] = {}
        for e in entities:
            self._by_slug[e.slug] = e
        for e in self._by_slug.values():
            for alias in e.aliases:
                self._by_alias.setdefault(api_slugify(alias), e)

    def __len__(self) -> int:
        return len(self._by_slug)

    def find(self, slug: str) -> WcsEntity | None:
        """Slug, then alias, then the depluralized slug and alias."""
        for candidate in dict.fromkeys((slug, api_depluralize(slug))):
            hit = self._by_slug.get(candidate) or self._by_alias.get(candidate)
            if hit is not None:
                return hit
        return None

    def resolve(self, name: str, polarity: Polarity) -> Reference | None:
        """Turn one model-named mention into a reference, or None if unusable.

        The name is resolved as the API would resolve it; if nothing matches,
        the slug it would be created under is proposed.
        """
        slug = api_slugify(name)
        if not slug:
            return None
        hit = self.find(slug)
        if hit is not None:
            return Reference(name, hit.slug, str(hit.id), polarity)
        return Reference(name, slug, None, polarity)


# ── The model ──

SYSTEM_PROMPT = """\
You link statements from West Coast Swing (WCS) lesson notes to the WCS \
concepts they refer to. The notes form a wiki where every concept has a page; \
your links decide which pages point at which.

Each statement is about one subject entity. For each statement, list the WCS \
concepts, techniques, patterns and drills it refers to OTHER THAN its subject.

Rules:
- Include foundational concepts whenever the statement is genuinely about \
them, even if it names a specific variant: "consistent stretch weight in the \
connected hand" refers to stretch and to connection. Name the general concept, \
not a paraphrase of the sentence. Examples of foundational concepts: \
connection, leading, following, frame, posture, stretch, compression, anchor, \
timing, rhythm, musicality, phrasing, weight transfer, momentum, slot. These \
are examples, not a vocabulary: never swap a specific concept for one of \
them. "Contrabody position", "center of gravity", "tempo" and "rotation" stay \
as they are, listed alongside any general concept the statement also \
discusses.
- Leading is the act of the leader asking or signalling something of the \
follower. Following is the act of the follower reacting to what the leader \
asks. Not everything a follower does is following: a follower's own choice, \
styling or body technique that is not a reaction to the lead does not refer \
to following.
- Two ideas get two names, never one combined name: "free arm" and \
"styling", not "free arm styling".
- Patterns and techniques named in the text count, including ones the \
subject is built from: "right side pass with underarm turn" refers to right \
side pass and to underarm turn. Never list the subject itself.
- Only link what the statement discusses. A word used in passing is not a \
reference: "connect the dots" is not about connection.
- Names are short noun phrases in lowercase, in their plain dictionary form, \
and always the noun rather than the verb or another form of the same idea: \
"redirect", not "redirection" or "redirecting"; "triple step", not "doing a \
good triple step"; "connection", not "connection quality" unless the statement \
is about connection quality as its own idea. Names are matched to wiki pages \
exactly, so use the common name.
- If a name from the list of names already in use fits, use it exactly.
- polarity is "deny" only when the statement explicitly says a concept is \
unrelated to, or not the same as, the subject. Everything else is "assert".
- Return every statement id you were given, with an empty list if it refers \
to nothing beyond its subject. Do not invent ids.

Output ONLY valid JSON — no markdown fences, no commentary before or after — \
in exactly this shape:
{"statements": [{"id": "<statement id>", "references": \
[{"name": "<concept>", "polarity": "assert"}]}]}
"""

#: Lenient on purpose: a model that adds a stray field to an otherwise good
#: answer should not cost the whole batch. Only the fields read are required.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": ["statements"],
    "properties": {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id", "references"],
                "properties": {
                    "id": {"type": "string"},
                    "references": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                            "required": ["name", "polarity"],
                            "properties": {
                                "name": {"type": "string"},
                                "polarity": {
                                    "type": "string",
                                    "enum": ["assert", "deny"],
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


def build_messages(
    batch: Sequence[Statement], names_in_use: Sequence[str] = ()
) -> list[LLMMessage]:
    """One batch of statements, as the model is shown them.

    No catalog of existing slugs: sending all of them with every call was most
    of the cost of a run. The model names concepts plainly and
    :meth:`Catalog.resolve` matches the names locally, as the API would.

    ``names_in_use`` is the short list of concepts this pass has already
    proposed that have no page yet, so later batches reuse "redirect" rather
    than minting "redirection" beside it. It grows with the new concepts, not
    with the corpus.
    """
    lines: list[str] = []
    if names_in_use:
        lines += [
            "Names already in use for concepts with no page yet:",
            ", ".join(names_in_use),
            "",
        ]
    lines += [
        "Statements (JSON):",
        json.dumps(
            [
                {"id": s.key, "subject": s.subject_slug, "kind": s.kind, "text": s.text}
                for s in batch
            ],
            ensure_ascii=False,
            indent=1,
        ),
    ]
    return [
        LLMMessage(role="system", content=SYSTEM_PROMPT),
        LLMMessage(role="user", content="\n".join(lines)),
    ]


def link_batch(
    llm: LLMClient,
    batch: Sequence[Statement],
    catalog: Catalog,
    names_in_use: Sequence[str] = (),
) -> dict[str, list[Reference]]:
    """One LLM call: the references for each statement in the batch.

    Ids the model invents are ignored; ids it leaves out come back with no
    entry, so they stay unlinked and the next run tries them again.
    Self-references and duplicates within a statement are dropped.
    """
    result = llm.generate_json(
        messages=build_messages(batch, names_in_use),
        json_schema=RESPONSE_SCHEMA,
        schema_name="links",
    )
    by_key = {s.key: s for s in batch}
    out: dict[str, list[Reference]] = {}
    for item in result.output_json.get("statements", []):
        statement = by_key.get(str(item.get("id", "")))
        if statement is None:
            continue
        refs: dict[tuple[str, str], Reference] = {}
        for raw in item.get("references", []):
            ref = catalog.resolve(
                str(raw.get("name", "")),
                "deny" if raw.get("polarity") == "deny" else "assert",
            )
            if ref is None or ref.target_slug == statement.subject_slug:
                continue
            refs.setdefault((ref.target_slug, ref.polarity), ref)
        out[statement.key] = list(refs.values())
    return out


# ── The links file (and watermark) ──


@dataclass
class LinksFile:
    """Every statement linked so far, and how. JSON on disk."""

    linker_version: str = LINKER_VERSION
    model: str = ""
    statements: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> LinksFile:
        """The file at ``path``; an empty one if it does not exist."""
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            linker_version=str(raw.get("linker_version", "")),
            model=str(raw.get("model", "")),
            statements=dict(raw.get("statements", {})),
        )

    def save(self, path: Path) -> None:
        """Write sorted, so a re-run that changes nothing changes no bytes."""
        payload = {
            "linker_version": self.linker_version,
            "model": self.model,
            "statements": dict(sorted(self.statements.items())),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def proposed_names(self) -> list[str]:
        """Names of concepts proposed under this version that have no page yet."""
        names: dict[str, str] = {}
        for entry in self.statements.values():
            if entry.get("linker_version") != LINKER_VERSION:
                continue
            for r in entry["references"]:
                if r["target_entity_id"] is None:
                    names.setdefault(r["target_slug"], r["name"].strip().lower())
        return sorted(names.values())

    def is_linked(self, statement: Statement) -> bool:
        """The watermark: linked already, under this linker version."""
        entry = self.statements.get(statement.key)
        return entry is not None and entry.get("linker_version") == LINKER_VERSION

    def record(
        self, statement: Statement, refs: Sequence[Reference], model: str
    ) -> None:
        """Store one statement's references, stamped with version and model."""
        self.statements[statement.key] = {
            "linker_version": LINKER_VERSION,
            "model": model,
            "subject_slug": statement.subject_slug,
            "source_id": str(statement.source_id) if statement.source_id else None,
            "session_date": statement.session_date.isoformat()
            if statement.session_date
            else None,
            "text": statement.text,
            "references": [asdict(r) for r in refs],
        }


def run_linking(
    statements: Sequence[Statement],
    catalog: Catalog,
    links: LinksFile,
    llm: LLMClient,
    *,
    model: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Any = None,
    workers: int = 1,
) -> dict[str, Any]:
    """Link every statement not already linked; record into ``links``.

    ``workers`` batches run at once; recording and ``on_batch`` (the save)
    happen under one lock. One bad response does not end a long run: that
    batch stays unlinked and the next run picks it up. With several workers,
    batches in flight at the same moment do not see each other's newly
    proposed names; the next run's names-in-use list catches them up.
    """
    todo = [s for s in statements if not links.is_linked(s)]
    skipped = len(statements) - len(todo)
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    lock = threading.Lock()
    stopped = threading.Event()
    linked = 0
    failed_batches = 0
    processed = 0

    def work(batch: list[Statement]) -> None:
        nonlocal linked, failed_batches, processed
        if stopped.is_set():
            return
        with lock:
            names = links.proposed_names()
        try:
            results = link_batch(llm, batch, catalog, names)
        except LLMError as exc:
            with lock:
                failed_batches += 1
                processed += len(batch)
            LOG.warning("link.batch_failed size=%s err=%s", len(batch), exc)
            return
        with lock:
            if stopped.is_set():  # stopped while in flight: see run_parallel
                return
            for statement in batch:
                if statement.key in results:
                    links.record(statement, results[statement.key], model)
                    linked += 1
            processed += len(batch)
            if on_batch is not None:
                on_batch()
            LOG.info(
                "link.batch done=%s/%s linked_total=%s", processed, len(todo), linked
            )

    run_parallel(work, batches, workers=workers, stopped=stopped)

    links.linker_version = LINKER_VERSION
    links.model = model
    return {
        "statements": len(statements),
        "skipped": skipped,
        "pending": len(todo),
        "linked": linked,
        "failed_batches": failed_batches,
    }


# ── Review ──


def summarize(links: LinksFile, keys: Iterable[str] | None = None) -> dict[str, Any]:
    """Counts for the statements in ``keys`` (all of them when None)."""
    wanted = set(keys) if keys is not None else set(links.statements)
    targets: Counter[str] = Counter()
    new: Counter[str] = Counter()
    denies = 0
    refs = 0
    for key in wanted:
        entry = links.statements.get(key)
        if entry is None:
            continue
        for r in entry["references"]:
            refs += 1
            targets[r["target_slug"]] += 1
            if r["target_entity_id"] is None:
                new[r["target_slug"]] += 1
            if r["polarity"] == "deny":
                denies += 1
    return {
        "statements": len(wanted),
        "references": refs,
        "denials": denies,
        "new_concepts": dict(new.most_common()),
        "top_targets": dict(targets.most_common(25)),
    }


def review_markdown(links: LinksFile, keys: Iterable[str]) -> str:
    """One block per statement: what it said, and what it was linked to.

    Meant for reading in Obsidian or an editor: the text, then each
    reference as a wiki-link — new concepts marked so they stand out.
    """
    lines = [f"# Linker review — version {LINKER_VERSION}", ""]
    for key in keys:
        entry = links.statements.get(key)
        if entry is None:
            continue
        kind = key.split(":", 1)[0]
        lines.append(
            f"## {entry['subject_slug']} · {kind} · {entry['session_date'] or '—'}"
        )
        lines.append("")
        lines.append(f"> {entry['text']}")
        lines.append("")
        if not entry["references"]:
            lines.append("- _(no references)_")
        for r in entry["references"]:
            mark = " **(new)**" if r["target_entity_id"] is None else ""
            deny = " — **denied**" if r["polarity"] == "deny" else ""
            lines.append(f"- {r['name']} → `{r['target_slug']}`{mark}{deny}")
        lines.append("")
    return "\n".join(lines)


# ── The overlay: references onto the rendered wiki ──

#: Namespace for the ids of concepts the pass proposes. Deterministic, so a
#: proposed concept keeps its id from render to render until the API mints a
#: real one.
_PROPOSED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "wcs-wiki/linker/proposed")


def _proposed_entity(slug: str, name: str) -> WcsEntity:
    return WcsEntity(
        id=uuid.uuid5(_PROPOSED_NAMESPACE, slug),
        slug=slug,
        canonical_name=" ".join(w[:1].upper() + w[1:] for w in name.split()) or slug,
        kind="concept",
        overview_md="",
        status="",
        external_origin={},
        aliases=[],
    )


@dataclass
class OverlayStats:
    """What an overlay added to the render."""

    statements: int = 0
    pairs: int = 0
    denied_pairs: int = 0
    proposed_pages: list[str] = field(default_factory=list)


def build_overlay(
    export: WcsWikiExport,
    links: LinksFile,
    merges: Any = None,
) -> tuple[WcsWikiExport, LinkOverlay, OverlayStats]:
    """Settle the links file into an overlay, and the export it renders over.

    Every reference becomes an event on its (subject, target) pair, dated by
    its statement's source. Per pair the newest event wins; on the same date a
    denial beats an assertion, since a correction note is the likelier of the
    two. A pair whose winner is a denial is dropped. Targets with no entity
    become proposed concepts, added to the returned export so they get pages
    and index entries like any other entity.

    ``merges`` (from :mod:`wiki_curator_cog.identity`) redirects references
    to and from merged-away entities onto their preferred entity; ``export``
    is then the merged export.
    """
    by_id = {str(e.id): e for e in export.entities}
    by_slug = {e.slug: e for e in export.entities}
    if merges:
        for old_id, new_id in merges.by_id.items():
            target = by_id.get(str(new_id))
            if target is not None:
                by_id[str(old_id)] = target
        for old_slug, new_slug in merges.by_slug.items():
            if new_slug in by_slug:
                by_slug[old_slug] = by_slug[new_slug]
    proposed: dict[str, WcsEntity] = {}
    events: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[str, int, str]]] = {}
    stats = OverlayStats()

    for entry in links.statements.values():
        subject = by_slug.get(entry.get("subject_slug", ""))
        if subject is None:
            continue
        stats.statements += 1
        date = entry.get("session_date") or ""
        for r in entry.get("references", []):
            target = by_id.get(r.get("target_entity_id") or "") or by_slug.get(
                r["target_slug"]
            )
            if target is None:
                target = proposed.get(r["target_slug"])
                if target is None:
                    target = _proposed_entity(r["target_slug"], r.get("name", ""))
                    proposed[r["target_slug"]] = target
            if target.id == subject.id:
                continue
            deny = r.get("polarity") == "deny"
            events.setdefault((subject.id, target.id), []).append(
                (date, 1 if deny else 0, "deny" if deny else "assert")
            )

    overlay = LinkOverlay()
    used_proposed: set[uuid.UUID] = set()
    for (subject_id, target_id), evts in events.items():
        evts.sort()
        if evts[-1][2] == "deny":
            stats.denied_pairs += 1
            continue
        n = sum(1 for e in evts if e[2] == "assert")
        overlay.mentions.setdefault(subject_id, {})[target_id] = n
        overlay.referenced_by.setdefault(target_id, {})[subject_id] = n
        used_proposed.add(target_id)
        stats.pairs += 1

    kept = [e for e in proposed.values() if e.id in used_proposed]
    stats.proposed_pages = sorted(e.slug for e in kept)
    augmented = export.model_copy(update={"entities": [*export.entities, *kept]})
    return augmented, overlay, stats
