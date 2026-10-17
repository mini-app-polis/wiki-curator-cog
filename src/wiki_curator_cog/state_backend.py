"""Where the curator's state is read from and written to (MINI-94, part 1).

The three passes' state — links, facets, prose — is held by the API
(ADR-006, MINI-89). :class:`ApiState` makes it the store: every command reads
each document from the API, and a run writes back the entries it changed as
it goes, so nothing paid for lives only on this machine. The local file is
kept as a cache, written in exactly the layout it always had, so the reviews,
git history and Obsidian keep working.

:class:`FileState` is the old behaviour, files only. It is what the tests
use, and what ``--state files`` asks for.

Three rules keep paid work from being lost:

- **Refuse on disagreement.** Before using the API's document, it is
  compared with the local file. If they differ, the file holds something the
  API does not — a run whose last write never arrived — or the API was
  changed elsewhere. Either way nothing is overwritten: the command stops and
  says to run ``state-verify`` and then ``state-import`` if the file is the
  one to keep.
- **Write as you go.** Changed entries are sent in batches during a run and
  flushed at the end, so a run cut short has sent all but its last batch.
- **Stop on a failed write.** If the API refuses a write, the run stops
  before it spends more. The file already has the result, so the rule above
  catches it next time.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log

from . import consolidate, generate, link, state_store

LOG = log.get_logger()

#: Push changed entries once this many are waiting, or this long has passed.
FLUSH_ENTRIES = 100
FLUSH_SECONDS = 20.0

StateObject = link.LinksFile | consolidate.FacetsFile | generate.ProseFile


class StateMismatchError(RuntimeError):
    """The local file and the API disagree; nothing was read or written."""


def _missing(path: Path, required: bool) -> None:
    if required:
        raise RuntimeError(f"state file not found: {path}")


class FileState:
    """The state in local JSON files: the curator as it was before MINI-94."""

    name = "files"

    def links(self, path: Path, *, required: bool = False) -> link.LinksFile | None:
        if not path.exists():
            _missing(path, required)
            return link.LinksFile()
        return link.LinksFile.load(path)

    def facets(
        self, path: Path, *, required: bool = False
    ) -> consolidate.FacetsFile | None:
        if not path.exists():
            _missing(path, required)
            return consolidate.FacetsFile()
        return consolidate.FacetsFile.load(path)

    def prose(self, path: Path, *, required: bool = False) -> generate.ProseFile | None:
        if not path.exists():
            _missing(path, required)
            return generate.ProseFile()
        return generate.ProseFile.load(path)

    def save(self, obj: StateObject, path: Path, *, final: bool = False) -> None:
        _ = final
        obj.save(path)


# ── Between the file objects and the API's documents ──


def _name_of(obj: StateObject) -> str:
    if isinstance(obj, link.LinksFile):
        return "links"
    if isinstance(obj, consolidate.FacetsFile):
        return "facets"
    return "prose"


def document_of(obj: StateObject) -> dict[str, Any]:
    """A file object as the API's document: plain JSON."""
    if isinstance(obj, link.LinksFile):
        return {
            "linker_version": obj.linker_version,
            "model": obj.model,
            "statements": obj.statements,
        }
    if isinstance(obj, consolidate.FacetsFile):
        return {
            "consolidator_versions": obj.consolidator_versions,
            "model": obj.model,
            "entities": obj.entities,
        }
    return {
        "generator_version": obj.generator_version,
        "model": obj.model,
        "entities": obj.entities,
    }


def object_of(name: str, doc: Any) -> StateObject:
    """The API's document as the curator's file object, in the file's layout.

    Entries are laid out as the passes write them — prose entries with their
    revisions first, revisions without the fields they never had — so saving
    the object writes the same bytes the file had.
    """
    if name == "links":
        return link.LinksFile(
            linker_version=doc.linker_version,
            model=doc.model,
            statements={
                k: v.model_dump(mode="json") for k, v in doc.statements.items()
            },
        )
    if name == "facets":
        return consolidate.FacetsFile(
            consolidator_versions=dict(doc.consolidator_versions),
            model=doc.model,
            entities={k: v.model_dump(mode="json") for k, v in doc.entities.items()},
        )
    entities: dict[str, dict[str, Any]] = {}
    for slug, entry in doc.entities.items():
        rest = entry.model_dump(mode="json", exclude={"revisions", "integrated"})
        entities[slug] = {
            "revisions": [
                r.model_dump(mode="json", exclude_none=True) for r in entry.revisions
            ],
            "integrated": list(entry.integrated),
            **rest,
        }
    return generate.ProseFile(
        generator_version=doc.generator_version, model=doc.model, entities=entities
    )


def _fingerprints(doc: dict[str, Any], entries_key: str) -> dict[str, str]:
    return {
        k: json.dumps(v, sort_keys=True, ensure_ascii=False)
        for k, v in doc[entries_key].items()
    }


def _header(doc: dict[str, Any], entries_key: str) -> str:
    return json.dumps(
        {k: v for k, v in doc.items() if k != entries_key}, sort_keys=True
    )


class ApiState(FileState):
    """The state in the API, with the local files as a checked cache."""

    name = "api"

    def __init__(self, client: Any) -> None:
        self.client = client
        #: Per document: each entry as last sent or read, and the header.
        self._sent: dict[str, dict[str, str]] = {}
        self._sent_header: dict[str, str] = {}
        self._last_flush: dict[str, float] = {}

    def _read(self, name: str, path: Path) -> StateObject:
        remote = getattr(self.client, f"get_wcs_wiki_{name}")()
        entries_key = state_store._ENTRIES[name]
        if path.exists():
            local = getattr(FileState(), name)(path)
            diff = state_store.compare(
                document_of(local),
                remote.model_dump(mode="json", exclude_none=True),
                entries_key,
            )
            if diff:
                raise StateMismatchError(
                    f"{path.name} differs from the API's {name} document "
                    f"({len(diff.missing)} not in the API, {len(diff.changed)} "
                    f"changed, {len(diff.extra)} only in the API"
                    + (f", header {sorted(diff.header)}" if diff.header else "")
                    + "). Nothing was read or written. Run state-verify; if the "
                    "file is the one to keep, run state-import, then try again."
                )
        obj = object_of(name, remote)
        doc = document_of(obj)
        self._sent[name] = _fingerprints(doc, entries_key)
        self._sent_header[name] = _header(doc, entries_key)
        self._last_flush[name] = time.monotonic()
        LOG.info("state.read source=api doc=%s entries=%s", name, len(doc[entries_key]))
        return obj

    def links(self, path: Path, *, required: bool = False) -> link.LinksFile | None:
        obj = self._read("links", path)
        assert isinstance(obj, link.LinksFile)
        return obj

    def facets(
        self, path: Path, *, required: bool = False
    ) -> consolidate.FacetsFile | None:
        obj = self._read("facets", path)
        assert isinstance(obj, consolidate.FacetsFile)
        return obj

    def prose(self, path: Path, *, required: bool = False) -> generate.ProseFile | None:
        obj = self._read("prose", path)
        assert isinstance(obj, generate.ProseFile)
        return obj

    def save(self, obj: StateObject, path: Path, *, final: bool = False) -> None:
        """Write the cache; send what changed once enough has, or at the end."""
        obj.save(path)
        name = _name_of(obj)
        if name not in self._sent:
            raise RuntimeError(f"{name} was not read from the API before saving")
        entries_key = state_store._ENTRIES[name]
        doc = document_of(obj)
        now = _fingerprints(doc, entries_key)
        sent = self._sent[name]
        changed = sorted(k for k, v in now.items() if sent.get(k) != v)
        header = _header(doc, entries_key)
        due = (
            final
            or len(changed) >= FLUSH_ENTRIES
            or time.monotonic() - self._last_flush[name] >= FLUSH_SECONDS
        )
        if not due or (not changed and header == self._sent_header[name]):
            return
        model = state_store._MODELS[name]
        head = {k: v for k, v in doc.items() if k != entries_key}
        unknown = state_store.unknown_fields(
            {**head, entries_key: {k: doc[entries_key][k] for k in changed}}, model
        )
        if unknown:
            raise state_store.UnknownFieldsError(
                f"{name}: fields the API does not store, nothing sent: {unknown[:10]}"
            )
        put = getattr(self.client, f"put_wcs_wiki_{name}")
        for i in range(0, max(len(changed), 1), state_store.DEFAULT_CHUNK):
            part = {
                k: doc[entries_key][k]
                for k in changed[i : i + state_store.DEFAULT_CHUNK]
            }
            put(model.model_validate({**head, entries_key: part}))
        for k in changed:
            sent[k] = now[k]
        self._sent_header[name] = header
        self._last_flush[name] = time.monotonic()
        LOG.info("state.write doc=%s entries=%s final=%s", name, len(changed), final)
