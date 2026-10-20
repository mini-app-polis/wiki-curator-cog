"""A lesson's way into the wiki, through the Lambda handler and the API's wire.

The unit tests run each job against an in-process store. These deliver real
SQS-shaped messages to ``worker.lambda_handler`` and let the jobs it asks for
come back as messages, until none are left — link, consolidate, generate,
materialise — with every call to the API going over HTTP through the shared
client and its contract models. They pin the things only the whole chain can
show: that a composed lesson ends as stored pages, that a repeated delivery
costs nothing, that a fold removes the folded page, and that a failure returns
the message instead of losing the work.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from mini_app_polis.api.contract import WcsWikiPagesWrite

from wiki_curator_cog import worker

from ..unit.test_link import _id
from .conftest import FakeApi, Harness

SOURCE = str(_id("s1"))
LINK = {"job": "link", "source_id": SOURCE}
A1 = f"attribution:{_id('a1')}"


def test_a_lesson_ends_as_stored_pages(harness: Harness, api: FakeApi) -> None:
    harness.llm.links = {A1: [{"name": "connection", "polarity": "assert"}]}

    assert harness.deliver(LINK) == []
    harness.drain()

    # Every pass ran, in the order the follow-ons name them.
    assert harness.delivered[0] == "link"
    assert {"consolidate", "generate", "materialise"} <= set(harness.delivered)
    assert harness.delivered.index("generate") < harness.delivered.index("materialise")

    # The state each pass paid for is in the API, not only in a temp folder.
    assert A1 in api.state["links"]["statements"]
    assert set(api.state["prose"]["entities"]) >= {"anchor-step", "stretch"}

    # Each page the lesson touched is stored, the linker's new concept included.
    assert {"anchor-step", "stretch", "connection"} <= set(api.pages)
    stretch = api.pages["stretch"]
    assert stretch["definition"] == "A definition."
    citation = stretch["by_teacher"][0]["statements"][0]["citation"]
    assert citation["source_id"] == SOURCE
    assert citation["public"] is False  # the fixture's source is private
    assert stretch["evidence_sources"] == [SOURCE]
    connection = api.pages["connection"]
    assert connection["by_teacher"] == []
    assert [r["slug"] for r in connection["referenced_by"]] == ["anchor-step"]
    assert connection["evidence_sources"] == [SOURCE]

    # Every follow-on names the lesson as its origin, and every page write
    # says so, with the message that wrote it.
    assert {j.get("origin_source_id") for j in api.enqueued} == {SOURCE}
    assert api.page_origins
    assert all(o == SOURCE and str(j).startswith("m-") for o, j in api.page_origins)
    # The consolidation stored the thin list, over the whole corpus.
    assert api.thin and all(e["page"] for e in api.thin.values())


def test_delivering_the_lesson_again_costs_nothing(
    harness: Harness, api: FakeApi
) -> None:
    harness.deliver(LINK)
    harness.drain()
    calls, pages, enqueued = dict(harness.llm.calls), dict(api.pages), len(api.enqueued)

    assert harness.deliver(LINK) == []

    assert harness.llm.calls == calls  # no model call
    assert api.pages == pages
    # Only the decisions still owed are asked for again; none of them pay.
    harness.drain()
    assert harness.llm.calls == calls
    assert all(j["job"] == "consolidate" for j in api.enqueued[enqueued:])


def test_a_fold_takes_the_folded_page_away(harness: Harness, api: FakeApi) -> None:
    harness.llm.parents = {"anchor-step": "stretch"}

    harness.deliver(LINK)
    harness.drain()

    assert "anchor-step" not in api.pages
    details = api.pages["stretch"]["details"]
    assert [d["slug"] for d in details] == ["anchor-step"]
    # Its statements moved to the page it folded into.
    teachers = api.pages["stretch"]["by_teacher"]
    texts = [s["text"] for t in teachers for s in t["statements"]]
    assert "Keep stretch in the connection through the anchor." in texts


def test_a_refused_page_write_returns_the_message(
    harness: Harness, api: FakeApi
) -> None:
    harness.deliver({"job": "generate", "entities": ["stretch"]})
    materialise = api.queue.pop(0)
    assert materialise == {"job": "materialise", "entities": ["stretch"]}

    api.pages_status = 503
    failures = harness.deliver(materialise)
    assert len(failures) == 1  # back on the queue, not lost
    assert api.pages == {}

    api.pages_status = 200
    assert harness.deliver(materialise, attempt="2") == []
    assert "stretch" in api.pages


def test_an_unreadable_message_is_reported_once_and_dead_lettered(
    harness: Harness, api: FakeApi
) -> None:
    with patch.object(worker, "post_run_finding") as report:
        first = harness.deliver_body("not json", attempt="1")
        again = harness.deliver_body("not json", attempt="2")

    assert len(first) == 1 and len(again) == 1
    report.assert_called_once()
    assert api.calls == []  # nothing ran, nothing was read


def test_the_follow_ons_cross_the_wire_as_jobs_the_worker_reads(
    harness: Harness, api: FakeApi
) -> None:
    harness.deliver(LINK)
    # What the worker posted is what the API enqueues, and what comes back.
    for job in api.queue:
        body = json.dumps({"type": "wiki.job", "version": 1, "payload": job})
        assert worker.job_of(body) == job


def test_the_fake_api_refuses_a_field_the_contract_would_drop(api: FakeApi) -> None:
    # What makes the tests above a check on the contract, not only the code.
    page = {"slug": "x", "name": "X", "kind": "concept", "not_a_field": 1}
    with pytest.raises(AssertionError, match="contract does not hold"):
        api._strict(WcsWikiPagesWrite, {"pages": [page]})
