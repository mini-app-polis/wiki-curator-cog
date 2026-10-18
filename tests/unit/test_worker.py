"""The Lambda worker: one wiki job per message (MINI-95)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mini_app_polis.api.contract import WcsWikiJobsAccepted

from wiki_curator_cog import worker

from .test_link import _export, _id
from .test_state_store import FakeApi
from .test_units import RouterLLM

SOURCE = str(_id("s1"))


def _body(payload: dict[str, Any], **over: Any) -> str:
    return json.dumps({"type": "wiki.job", "version": 1, "payload": payload, **over})


def _record(body: str, attempt: str = "1") -> dict[str, Any]:
    return {
        "messageId": f"m-{abs(hash(body)) % 1000}",
        "body": body,
        "attributes": {"ApproximateReceiveCount": attempt},
    }


class Api(FakeApi):
    """The state endpoints, plus the jobs endpoint the worker posts to."""

    def __init__(self) -> None:
        super().__init__()
        self.enqueued: list[list[dict[str, Any]]] = []

    def enqueue_wcs_wiki_jobs(self, payload: Any) -> WcsWikiJobsAccepted:
        jobs = [j.model_dump(mode="json", exclude_none=True) for j in payload.jobs]
        self.enqueued.append(jobs)
        return WcsWikiJobsAccepted(message_ids=[f"q-{i}" for i in range(len(jobs))])


# ── Reading a message ──


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("not json", "not JSON"),
        ("[]", "not an object"),
        (_body({"job": "link", "source_id": SOURCE}, version=2), "version 2"),
        (_body({"job": "link", "source_id": SOURCE}, type="x"), "type"),
        (json.dumps({"type": "wiki.job", "version": 1}), "payload"),
        (_body({"job": "link"}), "source_id"),
        (_body({"job": "dance"}), "unknown job"),
    ],
)
def test_an_unreadable_message_is_refused(body: str, why: str) -> None:
    with pytest.raises(worker.UnprocessableMessage, match=why):
        worker.job_of(body)


def test_a_good_message_gives_its_job() -> None:
    job = {"job": "generate", "entities": ["anchor"]}
    assert worker.job_of(_body(job)) == job


# ── Running one ──


def test_a_job_runs_against_the_api_and_sends_its_follow_ons(tmp_path: Path) -> None:
    api = Api()
    with (
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
    ):
        result = worker.run(
            {"job": "link", "source_id": SOURCE}, client=api, state_dir=tmp_path
        )
    assert result["linked"] == 4
    assert len(api.docs["links"]["statements"]) == 4
    assert [j["job"] for j in api.enqueued[0]] == ["consolidate", "generate"]
    assert result["enqueued"] == ["q-0", "q-1"]


def test_generate_sends_materialise_and_materialise_sends_nothing(
    tmp_path: Path,
) -> None:
    api = Api()
    with (
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
    ):
        worker.run(
            {"job": "generate", "entity": "stretch"}, client=api, state_dir=tmp_path
        )
        assert api.enqueued == [[{"job": "materialise", "entities": ["stretch"]}]]
        result = worker.run(
            {"job": "materialise", "entities": ["stretch"]},
            client=api,
            state_dir=tmp_path,
        )
    assert api.enqueued == [[{"job": "materialise", "entities": ["stretch"]}]]
    assert result["written"] == 1
    assert api.pages["stretch"]["definition"] == "A definition."


# ── The handler ──


def test_a_good_record_is_deleted_and_a_failed_one_returned() -> None:
    good = _record(_body({"job": "generate", "entity": "a"}))
    bad = _record(_body({"job": "generate", "entity": "b"}))

    def fake(body: str, *, budget_s: int, run_id: str) -> dict:
        if run_id == bad["messageId"]:
            raise RuntimeError("API down")
        return {"next": []}

    with (
        patch.object(worker, "load_secrets"),
        patch.object(worker, "process_message", side_effect=fake),
    ):
        out = worker.lambda_handler({"Records": [good, bad]}, None)
    assert out == {"batchItemFailures": [{"itemIdentifier": bad["messageId"]}]}


def test_an_unprocessable_message_is_reported_once_and_returned() -> None:
    first = _record("not json", attempt="1")
    later = _record("not json", attempt="2")
    with (
        patch.object(worker, "load_secrets"),
        patch.object(worker, "post_run_finding") as reported,
    ):
        assert worker.lambda_handler({"Records": [first]}, None)["batchItemFailures"]
        assert worker.lambda_handler({"Records": [later]}, None)["batchItemFailures"]
    assert reported.call_count == 1


def test_without_its_settings_every_record_comes_back() -> None:
    records = [_record(_body({"job": "generate", "entity": "a"}))]
    with patch.object(worker, "load_secrets", side_effect=RuntimeError("ssm")):
        out = worker.lambda_handler({"Records": records}, None)
    assert out == {"batchItemFailures": [{"itemIdentifier": records[0]["messageId"]}]}


def test_the_budget_is_lambdas_remaining_time() -> None:
    class Ctx:
        def get_remaining_time_in_millis(self) -> int:
            return 900_000

    assert worker._budget(Ctx()) == 900
    assert worker._budget(None) == 0  # local: no guard


# ── Run reports ──


class _Report:
    """Records what a job's run report was told."""

    def __init__(self) -> None:
        self.oks = 0
        self.issues: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}

    def ok(self, n: int = 1) -> None:
        self.oks += n

    def issue(self, reason: str, item: str, detail: str = "") -> None:
        self.issues.append((reason, item))

    def count(self, key: str, value: int) -> None:
        self.counts[key] = value


def test_a_clean_job_reports_counts_and_no_issue() -> None:
    report = _Report()
    worker.record(
        report,
        {"job": "generate", "entities": ["a"]},
        {
            "generated": 2,
            "failed": 0,
            "failed_slugs": [],
            "usage": {"calls": 2, "cost_usd": 0.0314},
            "enqueued": ["q-0"],
        },
    )
    assert report.oks == 2 and report.issues == []
    assert report.counts == {"model_calls": 2, "cost_usd_cents": 3, "jobs_enqueued": 1}


@pytest.mark.parametrize(
    ("job", "result", "issue"),
    [
        (
            {"job": "link", "source_id": SOURCE},
            {"source_id": SOURCE, "linked": 3, "failed_batches": 1},
            ("link_batch_failed", SOURCE),
        ),
        (
            {"job": "consolidate", "entities": ["a", "b"]},
            {"entities": ["a", "b"], "decided": 0, "failed_batches": 1},
            ("consolidate_batch_failed", "a, b"),
        ),
        (
            {"job": "generate", "entities": ["a"]},
            {"generated": 0, "failed": 1, "failed_slugs": ["a"]},
            ("generate_failed", "a"),
        ),
    ],
)
def test_work_left_undone_is_an_issue(job: dict, result: dict, issue: tuple) -> None:
    report = _Report()
    worker.record(report, job, result)
    assert report.issues == [issue]


def test_each_job_opens_its_own_run_report(tmp_path: Path) -> None:
    opened: list[tuple[str, str]] = []

    @contextmanager
    def fake_report(flow_name: str, *, repo: str, run_id: str):
        opened.append((flow_name, run_id))
        yield _Report()

    api = Api()
    with (
        patch.object(worker, "run_report", fake_report),
        patch.object(worker, "_state_client", return_value=api),
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
    ):
        worker.process_message(
            _body({"job": "materialise", "entities": ["stretch"]}),
            budget_s=0,
            run_id="m-9",
        )
    assert opened == [("wiki-materialise", "m-9")]
