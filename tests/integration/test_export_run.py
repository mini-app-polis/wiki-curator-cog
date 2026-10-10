"""An export, run end to end through its entry point.

One wake is one run: fetch the corpus, re-render the whole wiki, commit what
changed, push, and send one report. These tests run exactly that against a
real git remote, with only the Kaiano API stubbed. They pin three things the
unit tests cannot, because each depends on the parts working together:

  - A run lands the rendered wiki on the remote, removing what the export
    no longer contains, and reports it once.
  - A second run of the same corpus rewrites no page. The rebuild is
    idempotent, which is what makes a re-trigger safe — this cog has no
    queue, so a re-trigger by hand is its redelivery. (It does still append
    to log.md, commit and report; that is pinned as a strict xfail below.)
  - A run that fails before it renders leaves the remote untouched and
    reports one ERROR.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

import pytest
import respx

import wiki_curator_cog.flow as flow

from ..unit.export_builders import WcsEntity, WcsWikiExport
from .conftest import API_KEY, STALE_PAGE, remote_files, remote_head, serve_export


def _corpus():
    """One concept: enough to render a page and the indexes around it."""
    return WcsWikiExport(
        entities=[
            WcsEntity(
                id=uuid.uuid5(uuid.NAMESPACE_DNS, "entity-anchor-step"),
                slug="anchor-step",
                canonical_name="Anchor Step",
                kind="concept",
                overview_md="Neutral overview of anchor step.",
                status="draft",
            )
        ],
        exported_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


def _notified(api: respx.MockRouter) -> list[str]:
    """The text of every run report the API received."""
    texts = []
    for call in api.routes["notify"].calls:
        body = json.loads(call.request.content)
        texts.append(json.dumps(body["embeds"]))
    return texts


def test_a_run_lands_the_rendered_wiki_on_the_remote(
    origin: Path, run_env: Path, api: respx.MockRouter
) -> None:
    serve_export(api, _corpus())
    before = remote_head(origin)

    summary = flow.export_run(run_id="run-1")

    files = remote_files(origin)
    assert "concepts/anchor-step.md" in files
    assert STALE_PAGE not in files
    assert "CLAUDE.md" in files
    assert remote_head(origin) != before
    assert summary["committed"] is True
    assert "concepts/anchor-step.md" in summary["added"]
    assert STALE_PAGE in summary["removed"]

    export_call = api.routes["export"].calls.last
    assert export_call.request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert len(_notified(api)) == 1


def test_running_the_same_corpus_again_rewrites_no_page(
    origin: Path, run_env: Path, api: respx.MockRouter
) -> None:
    serve_export(api, _corpus())
    flow.export_run(run_id="run-1")

    summary = flow.export_run(run_id="run-2")

    assert summary["added"] == summary["removed"] == []
    # The render log gains a dated entry on every run; nothing else moves.
    assert summary["modified"] == ["log.md"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "log.md gains an entry on every run, so an identical rebuild still "
        "commits and reports — against what flow.record_summary and "
        "WikiRepo.staged_changes say a no-change run should do."
    ),
)
def test_running_the_same_corpus_again_commits_and_reports_nothing(
    origin: Path, run_env: Path, api: respx.MockRouter
) -> None:
    serve_export(api, _corpus())
    flow.export_run(run_id="run-1")
    head = remote_head(origin)
    reports = len(_notified(api))

    summary = flow.export_run(run_id="run-2")

    assert summary["committed"] is False
    assert remote_head(origin) == head
    assert len(_notified(api)) == reports


def test_a_run_that_cannot_fetch_the_corpus_leaves_the_remote_alone(
    origin: Path, run_env: Path, api: respx.MockRouter
) -> None:
    """The export route answers 503 unless a test serves a corpus."""
    before = remote_head(origin)

    with pytest.raises(Exception):  # noqa: B017 — the type is the client's
        flow.export_run(run_id="run-1")

    assert remote_head(origin) == before
    assert STALE_PAGE in remote_files(origin)
    reports = _notified(api)
    assert len(reports) == 1
    assert "export_failed" in reports[0]
