"""Fixtures for running wiki jobs end to end through the Lambda handler.

What is real: the handler (``worker.lambda_handler``), the message parsing,
every unit (link, consolidate, generate, materialise), the state backend, the
page builder, and the shared ``KaianoApiClient`` with the contract models it
serialises through — so a field the curator sends that the contract drops,
or a request the API would refuse, fails here.

What is stubbed: the Kaiano API, at the HTTP boundary (TEST-007), by
:class:`FakeApi` — a small in-memory version of the endpoints the worker
calls, which stores what it is sent the way the API does (each entry
upserted whole, the header replaced) and validates every body against the
contract. The model is a fake LLM that answers each pass by its schema.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from mini_app_polis.api.contract import (
    WcsWikiExportItem,
    WcsWikiFacetsState,
    WcsWikiJobsRequest,
    WcsWikiLinksState,
    WcsWikiPagesWrite,
    WcsWikiProseState,
    WcsWikiThinWrite,
)

from wiki_curator_cog import worker

from ..unit.test_link import _export
from ..unit.test_units import RouterLLM

API = "https://api.example"
API_KEY = "wiki-curator-test-key"

_STATE = {
    "links": (WcsWikiLinksState, "statements"),
    "facets": (WcsWikiFacetsState, "entities"),
    "prose": (WcsWikiProseState, "entities"),
}
_EMPTY = {
    "links": {"linker_version": "", "model": "", "statements": {}},
    "facets": {"consolidator_versions": {}, "model": "", "entities": {}},
    "prose": {"generator_version": "", "model": "", "entities": {}},
}


def _envelope(data: Any) -> dict[str, Any]:
    return {"data": data, "meta": {"count": 1, "total": 1, "version": "v1"}}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


class FakeApi:
    """The endpoints the worker calls, kept in memory."""

    def __init__(self, export: WcsWikiExportItem) -> None:
        self.export = export
        self.state: dict[str, dict[str, Any]] = json.loads(json.dumps(_EMPTY))
        self.pages: dict[str, dict[str, Any]] = {}
        #: Jobs the worker asked for, waiting to be delivered.
        self.queue: list[dict[str, Any]] = []
        #: Every job ever enqueued, in order.
        self.enqueued: list[dict[str, Any]] = []
        #: Status to answer pages POSTs with; 200 unless a test breaks it.
        self.pages_status = 200
        self.calls: list[str] = []
        #: Thin entities as the API stores them, by slug.
        self.thin: dict[str, dict[str, Any]] = {}
        #: Each pages POST's origin: (origin_source_id, origin_job_id).
        self.page_origins: list[tuple[str | None, str | None]] = []

    def _authorised(self, request: httpx.Request) -> None:
        assert request.headers.get("Authorization") == f"Bearer {API_KEY}"

    def _strict(self, model: Any, body: dict[str, Any]) -> Any:
        """The body as the API would read it, refusing anything it would drop."""
        parsed = model.model_validate(body)
        kept = _drop_none(parsed.model_dump(mode="json", exclude_unset=True))
        assert kept == _drop_none(body), "a field the contract does not hold was sent"
        return parsed

    def handle(self, request: httpx.Request) -> httpx.Response:
        self._authorised(request)
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/v1/wcs/wiki/export":
            return httpx.Response(
                200, json=_envelope(self.export.model_dump(mode="json"))
            )
        if path == "/v1/wcs/wiki/state/thin":
            if request.method == "GET":
                entities = [self.thin[s] for s in sorted(self.thin)]
                return httpx.Response(200, json=_envelope({"entities": entities}))
            thin = self._strict(WcsWikiThinWrite, json.loads(request.content))
            for e in thin.entities:
                self.thin[e.slug] = e.model_dump(mode="json")
            gone = [s for s in thin.remove if self.thin.pop(s, None) is not None]
            return httpx.Response(
                200,
                json=_envelope(
                    {
                        "written": len(thin.entities),
                        "removed": len(gone),
                        "total": len(self.thin),
                    }
                ),
            )
        if path.startswith("/v1/wcs/wiki/state/"):
            name = path.rsplit("/", 1)[-1]
            model, key = _STATE[name]
            if request.method == "GET":
                return httpx.Response(200, json=_envelope(self.state[name]))
            body = json.loads(request.content)
            self._strict(model, body)
            doc = self.state[name]
            doc.update({k: v for k, v in body.items() if k != key})
            doc[key].update(body[key])
            return httpx.Response(
                200,
                json=_envelope({"written": len(body[key]), "total": len(doc[key])}),
            )
        if request.method == "POST" and path == "/v1/wcs/wiki/pages":
            if self.pages_status != 200:
                return httpx.Response(
                    self.pages_status,
                    json={"error": {"code": "unavailable", "message": "down"}},
                )
            body = json.loads(request.content)
            write = self._strict(WcsWikiPagesWrite, body)
            self.page_origins.append(
                (
                    str(write.origin_source_id) if write.origin_source_id else None,
                    write.origin_job_id,
                )
            )
            for page in write.pages:
                self.pages[page.slug] = page.model_dump(mode="json")
            gone = [s for s in write.remove if self.pages.pop(s, None) is not None]
            return httpx.Response(
                200,
                json=_envelope(
                    {
                        "written": len(write.pages),
                        "removed": len(gone),
                        "total": len(self.pages),
                    }
                ),
            )
        if request.method == "POST" and path == "/v1/wcs/wiki/jobs":
            body = json.loads(request.content)
            jobs = self._strict(WcsWikiJobsRequest, body).jobs
            dumped = [j.model_dump(mode="json", exclude_none=True) for j in jobs]
            self.queue += dumped
            self.enqueued += dumped
            ids = [f"q-{len(self.enqueued) - len(jobs) + i}" for i in range(len(jobs))]
            return httpx.Response(202, json=_envelope({"message_ids": ids}))
        return httpx.Response(
            404, json={"error": {"code": "not_found", "message": path}}
        )


class Harness:
    """Deliver messages to the handler and drain the jobs they ask for."""

    def __init__(self, api: FakeApi, llm: RouterLLM) -> None:
        self.api = api
        self.llm = llm
        self.delivered: list[str] = []
        self._n = 0

    def deliver_body(self, body: str, attempt: str = "1") -> list[dict[str, str]]:
        self._n += 1
        event = {
            "Records": [
                {
                    "messageId": f"m-{self._n}",
                    "body": body,
                    "attributes": {"ApproximateReceiveCount": attempt},
                }
            ]
        }
        return worker.lambda_handler(event, None)["batchItemFailures"]

    def deliver(self, job: dict[str, Any], attempt: str = "1") -> list[dict[str, str]]:
        self.delivered.append(str(job.get("job")))
        body = json.dumps({"type": "wiki.job", "version": 1, "payload": job})
        return self.deliver_body(body, attempt)

    def drain(self, limit: int = 50) -> None:
        """Deliver every queued job, and the ones they ask for, until none are left."""
        for _ in range(limit):
            if not self.api.queue:
                return
            job = self.api.queue.pop(0)
            assert self.deliver(job) == [], f"{job} failed"
        raise AssertionError("the jobs never stopped asking for more")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeApi]:
    """The Kaiano API, served by a :class:`FakeApi` holding the unit export."""
    for name in ("ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(name, raising=False)
    # Outside production the client reads the _DEV name; set both.
    monkeypatch.setenv("KAIANO_API_BASE_URL", API)
    monkeypatch.setenv("KAIANO_API_BASE_URL_DEV", API)
    monkeypatch.setenv("WIKI_CURATOR_COG_API_KEY", API_KEY)
    fake = FakeApi(_export())
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
def harness(api: FakeApi) -> Iterator[Harness]:
    """The handler with no SSM, and a fake model in place of the real one."""
    llm = RouterLLM()
    with (
        patch.object(worker, "load_secrets"),
        patch.object(worker.link, "llm_from_env", return_value=(llm, "fake/m")),
    ):
        yield Harness(api, llm)
