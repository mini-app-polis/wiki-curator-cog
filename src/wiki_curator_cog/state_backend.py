"""Where the curator's state is read from and written to.

The three passes' state — links, facets, prose — is held by the API
(ADR-0006). :class:`ApiState` makes it the store: every command reads
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
import uuid
from pathlib import Path
from typing import Any

from mini_app_polis import logger as log
from mini_app_polis.api.contract import (
    WcsWikiPage,
    WcsWikiPagesWrite,
    WcsWikiThinEntity,
    WcsWikiThinWrite,
)

from . import consolidate, generate, link, state_store

LOG = log.get_logger()

#: Push changed entries once this many are waiting, or this long has passed.
FLUSH_ENTRIES = 100
FLUSH_SECONDS = 20.0

#: Page documents per POST: a page with hundreds of references is tens of KB.
PAGES_PER_POST = 50
#: Slugs removed per POST, within the contract's limit.
REMOVES_PER_POST = 500


def _max_length(model: Any, name: str) -> int:
    return int(
        next(
            m.max_length
            for m in model.model_fields[name].metadata
            if getattr(m, "max_length", None) is not None
        )
    )


#: Thin entities, and slugs removed, per POST: the contract's limits.
THIN_PER_POST = _max_length(WcsWikiThinWrite, "entities")
THIN_REMOVES_PER_POST = _max_length(WcsWikiThinWrite, "remove")

StateObject = link.LinksFile | consolidate.FacetsFile | generate.ProseFile


class StateMismatchError(RuntimeError):
    """The local file and the API disagree; nothing was read or written."""


def _missing(path: Path, required: bool) -> None:
    if required:
        raise RuntimeError(f"state file not found: {path}")


class FileState:
    """The state in local JSON files: the curator before the API was its store."""

    name = "files"

    def links(self, path: Path, *, required: bool = False) -> link.LinksFile | None:
        """Load the links file, or an empty one when it does not exist (unless ``required``)."""
        if not path.exists():
            _missing(path, required)
            return link.LinksFile()
        return link.LinksFile.load(path)

    def facets(
        self, path: Path, *, required: bool = False
    ) -> consolidate.FacetsFile | None:
        """Load the facets file, or an empty one when it does not exist (unless ``required``)."""
        if not path.exists():
            _missing(path, required)
            return consolidate.FacetsFile()
        return consolidate.FacetsFile.load(path)

    def prose(self, path: Path, *, required: bool = False) -> generate.ProseFile | None:
        """Load the prose file, or an empty one when it does not exist (unless ``required``)."""
        if not path.exists():
            _missing(path, required)
            return generate.ProseFile()
        return generate.ProseFile.load(path)

    def save(self, obj: StateObject, path: Path, *, final: bool = False) -> None:
        """Write ``obj`` to its file; ``final`` makes no difference here."""
        _ = final
        obj.save(path)

    def save_pages(
        self,
        pages: list[WcsWikiPage],
        remove: list[str],
        folder: Path,
        *,
        origin_source_id: uuid.UUID | str | None = None,
        origin_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Write each page document to ``folder/<slug>.json``; delete the removed.

        Page documents are not state the passes read back: the files are only
        a copy to read. Each is compared with the file it replaces, so a
        local run says which pages are new, changed or gone, as the API does.
        The origin is the API's to keep; the files have no place
        for it.
        """
        _ = origin_source_id, origin_job_id
        folder.mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        changed: list[str] = []
        for page in pages:
            path = folder / f"{page.slug}.json"
            document = page.model_dump(mode="json")
            if not path.exists():
                created.append(page.slug)
            elif json.loads(path.read_text()) != document:
                changed.append(page.slug)
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        removed_slugs: list[str] = []
        for slug in remove:
            path = folder / f"{slug}.json"
            if path.exists():
                path.unlink()
                removed_slugs.append(slug)
        return {
            "written": len(pages),
            "removed": len(removed_slugs),
            "created": sorted(created),
            "changed": sorted(changed),
            "removed_slugs": sorted(removed_slugs),
        }

    def save_thin(self, entities: list[WcsWikiThinEntity]) -> dict[str, int] | None:
        """Nothing: without the API, ``thin-review.md`` is the thin list."""
        _ = entities
        return None


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
        """Read the links document from the API, caching it at ``path``."""
        obj = self._read("links", path)
        assert isinstance(obj, link.LinksFile)
        return obj

    def facets(
        self, path: Path, *, required: bool = False
    ) -> consolidate.FacetsFile | None:
        """Read the facets document from the API, caching it at ``path``."""
        obj = self._read("facets", path)
        assert isinstance(obj, consolidate.FacetsFile)
        return obj

    def prose(self, path: Path, *, required: bool = False) -> generate.ProseFile | None:
        """Read the prose document from the API, caching it at ``path``."""
        obj = self._read("prose", path)
        assert isinstance(obj, generate.ProseFile)
        return obj

    def save_pages(
        self,
        pages: list[WcsWikiPage],
        remove: list[str],
        folder: Path,
        *,
        origin_source_id: uuid.UUID | str | None = None,
        origin_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Send the page documents to the API, in chunks; keep a copy in ``folder``.

        Pages go first, then the removals, so a run cut short never leaves a
        page removed and its replacement unwritten. A refused POST raises, and
        the job is retried; a re-sent page is a no-op. What is new, changed or
        gone is the API's answer, compared with what it stored; the copy here
        is a fresh folder each run and has nothing to compare against. Every
        chunk carries the origin — the source that started the work and the
        run that wrote it — so each revision can say what changed it.
        """
        super().save_pages(pages, remove, folder)
        source = uuid.UUID(str(origin_source_id)) if origin_source_id else None
        posts = [
            WcsWikiPagesWrite(
                pages=pages[i : i + PAGES_PER_POST],
                origin_source_id=source,
                origin_job_id=origin_job_id,
            )
            for i in range(0, len(pages), PAGES_PER_POST)
        ] + [
            WcsWikiPagesWrite(
                remove=remove[i : i + REMOVES_PER_POST],
                origin_source_id=source,
                origin_job_id=origin_job_id,
            )
            for i in range(0, len(remove), REMOVES_PER_POST)
        ]
        written = removed = 0
        created: list[str] = []
        changed: list[str] = []
        removed_slugs: list[str] = []
        for post in posts:
            result = self.client.put_wcs_wiki_pages(post)
            written += result.written
            removed += result.removed
            created += result.created
            changed += result.changed
            removed_slugs += result.removed_slugs
        LOG.info(
            "pages.write written=%s created=%s changed=%s removed=%s",
            written,
            len(created),
            len(changed),
            removed,
        )
        return {
            "written": written,
            "removed": removed,
            "created": sorted(created),
            "changed": sorted(changed),
            "removed_slugs": sorted(removed_slugs),
        }

    def save_thin(self, entities: list[WcsWikiThinEntity]) -> dict[str, int]:
        """Make the API's thin list this one: upsert what differs, remove the rest.

        The stored list is read first, so an entity that has not changed is
        not sent again and a slug that has stopped being thin is removed. Upserts go
        before removals, in chunks the contract allows. No model call.
        """
        stored = {
            e.slug: e.model_dump(mode="json")
            for e in self.client.get_wcs_wiki_thin().entities
        }
        current = {e.slug: e for e in entities}
        send = [
            current[s]
            for s in sorted(current)
            if stored.get(s) != current[s].model_dump(mode="json")
        ]
        remove = sorted(set(stored) - set(current))
        posts = [
            WcsWikiThinWrite(entities=send[i : i + THIN_PER_POST])
            for i in range(0, len(send), THIN_PER_POST)
        ] + [
            WcsWikiThinWrite(remove=remove[i : i + THIN_REMOVES_PER_POST])
            for i in range(0, len(remove), THIN_REMOVES_PER_POST)
        ]
        written = removed = 0
        for post in posts:
            result = self.client.put_wcs_wiki_thin(post)
            written += result.written
            removed += result.removed
        summary = {"total": len(current), "written": written, "removed": removed}
        LOG.info("thin.write %s", summary)
        return summary

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
