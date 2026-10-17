"""Identity fixes from notes: two names, one thing (ADR-005, MINI-90).

A note can say that two entities are the same thing and which name to use;
the extraction records that as a relation of kind ``alias_of``, from the name
not to use to the preferred one. A later note can say they are different —
``distinct_from`` — which undoes it. Both are dated through their source, and
for each pair the newest word wins.

The merge happens here, when the wiki is built, never in the database. The
preferred entity absorbs the other's statements, relations and references and
lists its name as an alias; the other entity disappears from the build, so
its page goes and links resolve to the preferred page. Because nothing upstream
is changed, a later ``distinct_from`` simply splits them again on the next
build.

Statement ids are untouched, so the linker's and the generator's watermarks
stay valid: the preferred entity just finds statements it has not integrated
yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .render import WcsEntity, WcsWikiExport

#: Relation kinds that merge, in the extraction's snake_case and the
#: hyphenated form, both accepted.
ALIAS_KINDS: frozenset[str] = frozenset({"alias_of", "same_as"})
#: Relation kinds that keep two entities apart, undoing an earlier alias.
DISTINCT_KINDS: frozenset[str] = frozenset({"distinct_from", "not_alias_of"})


def _kind(raw: str) -> str:
    return raw.strip().lower().replace("-", "_").replace(" ", "_")


@dataclass
class Merges:
    """Which entities were folded into which, by id and by slug."""

    #: merged-away id → preferred id
    by_id: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    #: merged-away slug → preferred slug
    by_slug: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.by_id)


def find_merges(export: WcsWikiExport) -> Merges:
    """Settle every alias/distinct claim into a set of merges.

    Per unordered pair the newest claim wins; undated claims (operator
    additions) count as oldest. The winning ``alias_of`` points from the
    merged-away entity to the preferred one. Chains resolve to their end
    (A→B, B→C folds A and B into C). Every entity on a cycle is left
    standing, since the claims do not agree on which name is preferred.
    """
    entities = {e.id: e for e in export.entities}
    dates = {
        s.id: (s.session_date.isoformat() if s.session_date else "")
        for s in export.sources
    }

    latest: dict[frozenset[uuid.UUID], tuple[str, int, str, uuid.UUID, uuid.UUID]] = {}
    for rel in export.relations:
        kind = _kind(rel.relation_kind)
        if kind not in ALIAS_KINDS and kind not in DISTINCT_KINDS:
            continue
        if rel.from_entity_id not in entities or rel.to_entity_id not in entities:
            continue
        if rel.from_entity_id == rel.to_entity_id:
            continue
        date = dates.get(rel.source_id, "") if rel.source_id else ""
        # On the same date, keeping apart beats merging: the cautious reading.
        rank = 1 if kind in DISTINCT_KINDS else 0
        event = (date, rank, kind, rel.from_entity_id, rel.to_entity_id)
        pair = frozenset((rel.from_entity_id, rel.to_entity_id))
        if pair not in latest or event[:2] > latest[pair][:2]:
            latest[pair] = event

    edges: dict[uuid.UUID, uuid.UUID] = {
        frm: to for (_, _, kind, frm, to) in latest.values() if kind in ALIAS_KINDS
    }

    merges = Merges()
    for start in sorted(edges, key=lambda i: entities[i].slug):
        seen = [start]
        current: uuid.UUID | None = start
        while current in edges:
            nxt = edges[current]
            if nxt in seen:  # a cycle: everything on it is left standing
                current = None
                break
            seen.append(nxt)
            current = nxt
        if current is not None:
            merges.by_id[start] = current
    merges.by_slug = {
        entities[frm].slug: entities[to].slug for frm, to in merges.by_id.items()
    }
    return merges


def apply_merges(export: WcsWikiExport, merges: Merges) -> WcsWikiExport:
    """The export with every merged-away entity folded into its preferred one.

    Content rows are re-pointed, the merged-away entities removed, their
    names and aliases added to the preferred entity's aliases, and relations
    that now point an entity at itself — the ``alias_of`` claim among them —
    dropped.
    """
    if not merges:
        return export
    m = merges.by_id

    def to(entity_id: uuid.UUID) -> uuid.UUID:
        return m.get(entity_id, entity_id)

    extra_aliases: dict[uuid.UUID, list[str]] = {}
    for e in export.entities:
        if e.id in m:
            extra_aliases.setdefault(m[e.id], []).extend([e.canonical_name, *e.aliases])

    entities: list[WcsEntity] = []
    for e in export.entities:
        if e.id in m:
            continue
        added = [a for a in extra_aliases.get(e.id, []) if a and a not in e.aliases]
        entities.append(
            e.model_copy(update={"aliases": [*e.aliases, *dict.fromkeys(added)]})
            if added
            else e
        )

    relations = []
    for r in export.relations:
        frm, dst = to(r.from_entity_id), to(r.to_entity_id)
        if frm == dst:
            continue
        relations.append(
            r.model_copy(update={"from_entity_id": frm, "to_entity_id": dst})
        )

    return export.model_copy(
        update={
            "entities": entities,
            "attributions": [
                a.model_copy(update={"entity_id": to(a.entity_id)})
                for a in export.attributions
            ],
            "definitions": [
                d.model_copy(update={"entity_id": to(d.entity_id)})
                for d in export.definitions
            ],
            "relations": relations,
            "drill_purposes": [
                p.model_copy(update={"drill_entity_id": to(p.drill_entity_id)})
                for p in export.drill_purposes
            ],
            "technique_requirements": [
                r.model_copy(update={"technique_entity_id": to(r.technique_entity_id)})
                for r in export.technique_requirements
            ],
        }
    )


def resolve(export: WcsWikiExport) -> tuple[WcsWikiExport, Merges]:
    """Find and apply every merge the notes call for."""
    merges = find_merges(export)
    return apply_merges(export, merges), merges
