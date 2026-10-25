"""Moving the curator's state into the API without losing what it cost.

The three passes keep their state — and their watermarks — in
``links.json``, ``facets.json`` and ``prose.json``. Those were paid for in
model calls (about $33 to build), so moving them to the API
(ADR-0006) is an import, never a relink or a regeneration, and it is
checked before anything relies on it:

- **Nothing unknown is sent.** Every entry is checked against the contract
  first. A field the API does not store would otherwise be dropped in
  transit, since response models tolerate unknown fields; here it stops the
  import before the first write.
- **Re-sendable.** Entries go up in chunks, and each chunk is an upsert, so
  an import cut short is simply run again.
- **Verified.** ``verify`` reads every document back and compares it,
  entry by entry, with the files. Pass means the API holds exactly what the
  files hold — every key, version, reference, revision and integrated
  statement — so the watermarks hold and no work is redone.

The files stay where they are, in git, until the curator reads the API
instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mini_app_polis.api.contract import (
    WcsWikiFacetsState,
    WcsWikiLinksState,
    WcsWikiProseState,
)
from pydantic import BaseModel

from . import consolidate, generate, link

#: Entries per POST. Prose entries carry their revisions, a few kilobytes
#: each; 250 keeps a request around a megabyte.
DEFAULT_CHUNK = 250

DOCS: tuple[str, ...] = ("links", "facets", "prose")
_MODELS: dict[str, type[BaseModel]] = {
    "links": WcsWikiLinksState,
    "facets": WcsWikiFacetsState,
    "prose": WcsWikiProseState,
}
#: Each document's entry map: links by statement key, the others by slug.
_ENTRIES = {"links": "statements", "facets": "entities", "prose": "entities"}


# ── The files, as documents ──


def local_documents(state_dir: Path) -> dict[str, dict[str, Any]]:
    """The three files as the API's documents: plain JSON, nothing dropped.

    Loaded through the curator's own file classes, so an older file layout
    (facets.json's single ``consolidator_version``) reads as it does
    everywhere else.
    """
    links = link.LinksFile.load(state_dir / "links.json")
    facets = consolidate.FacetsFile.load(state_dir / "facets.json")
    prose = generate.ProseFile.load(state_dir / "prose.json")
    return {
        "links": {
            "linker_version": links.linker_version,
            "model": links.model,
            "statements": links.statements,
        },
        "facets": {
            "consolidator_versions": facets.consolidator_versions,
            "model": facets.model,
            "entities": facets.entities,
        },
        "prose": {
            "generator_version": prose.generator_version,
            "model": prose.model,
            "entities": prose.entities,
        },
    }


def unknown_fields(raw: Any, model: Any, path: str = "") -> list[str]:
    """Paths in ``raw`` that ``model`` has no field for, at any depth."""
    out: list[str] = []
    if isinstance(model, type) and issubclass(model, BaseModel):
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            info = model.model_fields.get(key)
            if info is None:
                out.append(f"{path}.{key}" if path else key)
                continue
            out += unknown_fields(
                value, info.annotation, f"{path}.{key}" if path else key
            )
        return out
    args: tuple[Any, ...] = tuple(getattr(model, "__args__", ()) or ())
    origin = getattr(model, "__origin__", None)
    if origin is dict and isinstance(raw, dict) and len(args) == 2:
        for key, value in raw.items():
            out += unknown_fields(value, args[1], f"{path}[{key}]")
    elif origin is list and isinstance(raw, list) and args:
        for i, value in enumerate(raw):
            out += unknown_fields(value, args[0], f"{path}[{i}]")
    else:  # a union (X | None): whichever member is a model
        for arg in args:
            out += unknown_fields(raw, arg, path)
    return out


def canonical(value: Any) -> Any:
    """JSON with null-valued keys dropped: how both sides are compared."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


# ── Import ──


class UnknownFieldsError(RuntimeError):
    """The files hold fields the API would drop; nothing was sent."""


def import_state(
    client: Any,
    state_dir: Path,
    *,
    chunk: int = DEFAULT_CHUNK,
    only: tuple[str, ...] = DOCS,
) -> dict[str, Any]:
    """Send the files to the API, in chunks. Safe to run again.

    Checks every document for unknown fields before sending any of them.
    """
    docs = local_documents(state_dir)
    unknown = {
        name: found[:10]
        for name in only
        if (found := unknown_fields(docs[name], _MODELS[name]))
    }
    if unknown:
        raise UnknownFieldsError(
            f"fields the API does not store, nothing sent: {json.dumps(unknown)}"
        )

    summary: dict[str, Any] = {}
    for name in only:
        doc = docs[name]
        entries_key = _ENTRIES[name]
        header = {k: v for k, v in doc.items() if k != entries_key}
        keys = sorted(doc[entries_key])
        put = getattr(client, f"put_wcs_wiki_{name}")
        total = 0
        chunks = 0
        # An empty document still records its header.
        for i in range(0, max(len(keys), 1), chunk):
            part = {k: doc[entries_key][k] for k in keys[i : i + chunk]}
            result = put(_MODELS[name].model_validate({**header, entries_key: part}))
            total = result.total
            chunks += 1
        summary[name] = {"entries": len(keys), "chunks": chunks, "total_in_api": total}
    return summary


# ── Verify ──


@dataclass
class Difference:
    """How one document in the API differs from its file."""

    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    header: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.missing or self.extra or self.changed or self.header)


def compare(
    local: dict[str, Any], remote: dict[str, Any], entries_key: str
) -> Difference:
    """Entry by entry; headers field by field. Nulls count as absent."""
    a, b = canonical(local), canonical(remote)
    ea, eb = a.get(entries_key, {}), b.get(entries_key, {})
    diff = Difference(
        missing=sorted(set(ea) - set(eb)),
        extra=sorted(set(eb) - set(ea)),
        changed=sorted(k for k in set(ea) & set(eb) if ea[k] != eb[k]),
    )
    for k in (set(a) | set(b)) - {entries_key}:
        if a.get(k) != b.get(k):
            diff.header[k] = {"file": a.get(k), "api": b.get(k)}
    return diff


def verify_state(
    client: Any, state_dir: Path, *, only: tuple[str, ...] = DOCS
) -> dict[str, Any]:
    """Compare what the API holds with the files. ``ok`` only if identical."""
    docs = local_documents(state_dir)
    out: dict[str, Any] = {}
    for name in only:
        remote = getattr(client, f"get_wcs_wiki_{name}")()
        diff = compare(
            docs[name],
            remote.model_dump(mode="json", exclude_none=True),
            _ENTRIES[name],
        )
        out[name] = {
            "entries": len(docs[name][_ENTRIES[name]]),
            "ok": not diff,
            "missing": len(diff.missing),
            "extra": len(diff.extra),
            "changed": len(diff.changed),
            "examples": (diff.missing + diff.changed + diff.extra)[:10],
            "header": diff.header,
        }
    out["ok"] = all(out[name]["ok"] for name in only)
    return out
