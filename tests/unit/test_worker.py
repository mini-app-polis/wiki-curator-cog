"""The Lambda worker: one wiki job per message."""

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
        (_body({"job": "dance"}), "not a valid wiki.job"),
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


def test_follow_ons_go_in_requests_the_contract_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A job can name more follow-ons than one enqueue request carries.
    monkeypatch.setattr(worker, "_ENQUEUE_PER_REQUEST", 2)
    follow = [{"job": "materialise", "entities": [f"p{i}"]} for i in range(5)]
    api = Api()
    with (
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
        patch.object(worker.units, "handle", return_value={"job": "x", "next": follow}),
    ):
        result = worker.run({"job": "materialise"}, client=api, state_dir=tmp_path)
    assert [len(batch) for batch in api.enqueued] == [2, 2, 1]
    assert len(result["enqueued"]) == 5


# ── The handler ──


def test_a_good_record_is_deleted_and_a_failed_one_returned() -> None:
    good = _record(_body({"job": "generate", "entity": "a"}))
    bad = _record(_body({"job": "generate", "entity": "b"}))

    def fake(body: str, *, run_id: str, context: Any = None) -> dict:
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


def test_the_job_is_given_lambdas_context() -> None:
    """The deadline is read from it, so a job stops before Lambda kills it."""
    record = _record(_body({"job": "generate", "entity": "a"}))
    ctx = object()
    with (
        patch.object(worker, "load_secrets"),
        patch.object(worker, "process_message", return_value={}) as run,
    ):
        worker.lambda_handler({"Records": [record]}, ctx)
    assert run.call_args.kwargs["context"] is ctx


# ── Run reports ──


class _Report:
    """Records what a job's run report was told."""

    def __init__(self) -> None:
        self.oks = 0
        self.issues: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}
        self.outcomes: list[tuple[str, str, str]] = []

    def ok(self, n: int = 1) -> None:
        self.oks += n

    def issue(self, reason: str, item: str, detail: str = "") -> None:
        self.issues.append((reason, item))

    def count(self, key: str, value: int) -> None:
        self.counts[key] = value

    def send(self, *, notable: bool = False, **_kw: Any) -> None:
        self.sent_notable = notable

    def created(self, kind: str, item: str) -> None:
        self.outcomes.append(("+", kind, item))

    def updated(self, kind: str, item: str) -> None:
        self.outcomes.append(("~", kind, item))

    def removed(self, kind: str, item: str) -> None:
        self.outcomes.append(("-", kind, item))


def test_a_clean_job_reports_counts_and_no_issue() -> None:
    report = _Report()
    assert worker.record(
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


def test_a_rebuild_reports_what_changed_not_only_what_it_rewrote() -> None:
    report = _Report()
    worker.record(
        report,
        {"job": "materialise"},
        {
            "written": 1061,
            "deleted": 1,
            "created": ["stretch"],
            "changed": ["anchor", "frame"],
            "removed_slugs": ["old-name"],
        },
    )
    assert report.oks == 1061
    assert report.counts["pages_unchanged"] == 1058
    assert report.outcomes == [
        ("+", "page", "stretch"),
        ("~", "page", "anchor"),
        ("~", "page", "frame"),
        ("-", "page", "old-name"),
    ]


def test_each_pass_names_what_it_changed() -> None:
    link_report, fold_report, prose_report = _Report(), _Report(), _Report()
    worker.record(
        link_report,
        {"job": "link", "source_id": SOURCE},
        {"linked": 2, "references": ["anchor → frame", "stretch → anchor (denied)"]},
    )
    worker.record(
        fold_report,
        {"job": "consolidate", "entities": ["side-pass-left"]},
        {"decided": 1, "folds": ["side-pass-left → side-pass"]},
    )
    worker.record(
        prose_report,
        {"job": "generate", "entities": ["anchor", "frame"]},
        {"generated": 1, "generated_slugs": ["anchor"]},
    )
    assert link_report.outcomes == [
        ("+", "reference", "anchor → frame"),
        ("+", "reference", "stretch → anchor (denied)"),
    ]
    assert fold_report.outcomes == [("~", "fold", "side-pass-left → side-pass")]
    assert prose_report.outcomes == [("~", "prose", "anchor")]


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


@pytest.mark.parametrize(
    ("job", "result"),
    [
        ({"job": "materialise"}, {"written": 1061, "deleted": 0}),
        ({"job": "materialise", "entities": ["a"]}, {"written": 0, "deleted": 1}),
        ({"job": "link", "source_id": SOURCE}, {"linked": 4}),
        (
            {"job": "consolidate", "entities": ["a"]},
            {"decided": 0, "usage": {"calls": 1}},
        ),
        ({"job": "generate", "entities": ["a"]}, {"generated": 0, "enqueued": ["q-0"]}),
    ],
)
def test_a_job_that_did_something_is_posted(job: dict, result: dict) -> None:
    """A clean rebuild of every page used to be a log line and nothing in
    Discord (2026-10-18); every other queue-driven cog posts such a run."""
    assert worker.record(_Report(), job, result) is True


def test_a_job_with_nothing_left_to_do_stays_a_log_line() -> None:
    """A duplicate delivery meeting its watermark is not news."""
    result = {"linked": 0, "usage": {"calls": 0}, "enqueued": []}
    assert (
        worker.record(_Report(), {"job": "link", "source_id": SOURCE}, result) is False
    )


def test_each_job_opens_its_own_run_report(tmp_path: Path) -> None:
    opened: list[tuple[str, str]] = []
    sent: list[bool] = []

    @contextmanager
    def fake_report(flow_name: str, *, repo: str, run_id: str, version: str):
        opened.append((flow_name, run_id))
        report = _Report()
        yield report
        sent.append(report.sent_notable)

    api = Api()
    with (
        patch.object(worker, "run_report", fake_report),
        patch.object(worker, "_state_client", return_value=api),
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
    ):
        worker.process_message(
            _body({"job": "materialise", "entities": ["stretch"]}),
            run_id="m-9",
        )
    assert opened == [("wiki-materialise", "m-9")]
    assert sent == [True]


def test_a_consolidation_counts_its_thin_list() -> None:
    report = _Report()
    worker.record(
        report,
        {"job": "consolidate", "entities": ["a"]},
        {"decided": 1, "thin": {"total": 12, "written": 2, "removed": 1}},
    )
    assert (
        report.counts["thin_entities"],
        report.counts["thin_written"],
        report.counts["thin_removed"],
    ) == (12, 2, 1)


@pytest.mark.parametrize(
    ("job", "result"),
    [
        ({"job": "link", "source_id": SOURCE}, {"linked": 3}),
        ({"job": "materialise"}, {"written": 9}),
    ],
)
def test_any_job_counts_a_thin_list_refresh(job: dict, result: dict) -> None:
    report = _Report()
    worker.record(
        report, job, {**result, "thin": {"total": 5, "written": 1, "removed": 0}}
    )
    assert report.counts["thin_entities"] == 5
    assert report.counts["thin_written"] == 1


def test_the_worker_stores_its_message_id_on_the_pages(tmp_path: Path) -> None:
    api = Api()
    with (
        patch.object(worker, "_fetch_export", return_value=_export()),
        patch.object(worker.link, "llm_from_env", return_value=(RouterLLM(), "f/m")),
    ):
        worker.run(
            {"job": "materialise", "entities": ["stretch"], "origin_source_id": SOURCE},
            client=api,
            state_dir=tmp_path,
            run_id="m-7",
        )
    assert [(str(p.origin_source_id), p.origin_job_id) for p in api.page_posts] == [
        (SOURCE, "m-7")
    ]
