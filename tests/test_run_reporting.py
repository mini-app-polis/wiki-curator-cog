"""One run, one report — however the run ends.

Findings this cog posts through :mod:`wiki_curator_cog.api_client` are
graded results and stay rows. This is the separate question of whether
the export itself ran, which is not a finding and is not persisted.

The reporting used to live in ``main.wiki_curator_router`` and only
covered the success path; a crash was reported by a Prefect state hook.
With Prefect gone the report is the only channel, so these tests care as
much about the failure path as the happy one.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import wiki_curator_cog.flow as flow
from wiki_curator_cog._deadline import RunOutOfTime


def _summary(
    added=(),
    modified=(),
    removed=(),
    *,
    rendered: int = 2505,
    sources: int = 3,
    source_pages: int | None = None,
    dropped: list[tuple[str, str]] | None = None,
) -> dict:
    """One export summary, in the shape ``_export`` now produces.

    ``rendered`` is very nearly a constant — every page is rewritten every
    run — so the three change lists are what carry meaning.
    """
    return {
        "entities": 12,
        "sources": sources,
        "instructors": 2,
        "pages_rendered": rendered,
        "added": list(added),
        "modified": list(modified),
        "removed": list(removed),
        "committed": bool(added or modified or removed),
        "source_pages": sources if source_pages is None else source_pages,
        "dropped": [] if dropped is None else dropped,
    }


def _config(budget: int = 0) -> SimpleNamespace:
    """A config stub. Budget 0 disables the deadline, which suits a test."""
    return SimpleNamespace(run_timeout_seconds=budget)


def _run(summary_or_exc, *, budget: int = 0):
    """Run export_run with _export stubbed.

    Returns the patched ``send`` (the success channel) and
    ``post_run_finding`` (the failure channel) so a test can assert that
    exactly one of them fired.
    """
    kwargs = (
        {"side_effect": summary_or_exc}
        if isinstance(summary_or_exc, BaseException)
        else {"return_value": summary_or_exc}
    )
    with (
        patch.object(flow, "load_config", return_value=_config(budget)),
        patch.object(flow, "_export", **kwargs),
        patch.object(flow.RunReport, "send", autospec=True) as send,
        patch.object(flow, "post_run_finding") as finding,
    ):
        try:
            result = flow.export_run(run_id="run-1")
        except BaseException as exc:  # noqa: BLE001 — the test inspects it
            return send, finding, None, exc
    return send, finding, result, None


# ── exactly one message, on exactly one channel ─────────────────────────


def test_a_successful_run_sends_one_report() -> None:
    send, finding, result, exc = _run(_summary(modified=["a.md", "b.md"]))

    assert exc is None
    assert result["modified"] == ["a.md", "b.md"]
    send.assert_called_once()
    finding.assert_not_called()
    report = send.call_args.args[0]
    assert report.repo == "wiki-curator-cog"
    assert report.run_id == "run-1"
    assert report.severity == "SUCCESS"
    assert report.processed == 2


def test_a_failed_run_reports_error_and_re_raises() -> None:
    """PIPE-021, and the severity the Prefect hook used to carry.

    run_report() would have sent WARN here — its severity is derived, and a
    report has no verb for ERROR. A run that died on a rejected push must
    not read like one that dropped a dangling reference.
    """
    send, finding, _, exc = _run(RuntimeError("push rejected"))

    assert isinstance(exc, RuntimeError)
    finding.assert_called_once()
    # ...and the report is NOT also sent. One run, one message.
    send.assert_not_called()

    args, kwargs = finding.call_args
    assert args[1] == "ERROR"
    assert kwargs["repo"] == "wiki-curator-cog"
    assert kwargs["run_id"] == "run-1"
    assert "RuntimeError" in kwargs["text"]


def test_a_run_that_outlives_its_budget_is_reported_not_hung() -> None:
    """The deadline failure travels the ordinary path, so Railway is freed."""
    send, finding, _, exc = _run(RunOutOfTime("stopped early"))

    assert isinstance(exc, RunOutOfTime)
    finding.assert_called_once()
    send.assert_not_called()
    assert finding.call_args.args[1] == "ERROR"


def test_a_run_killed_by_sigterm_still_says_what_it_did() -> None:
    """BaseException, not Exception — a deploy mid-render still reports."""
    send, finding, _, exc = _run(KeyboardInterrupt())

    assert isinstance(exc, KeyboardInterrupt)
    finding.assert_called_once()
    send.assert_not_called()


def test_the_report_carries_the_run_id_it_was_given() -> None:
    """The library's fallback resolves Prefect ids and answers 'local-run'."""
    send, _, _, _ = _run(_summary(modified=["a.md"]))
    assert send.call_args.args[0].run_id == "run-1"


def test_the_report_says_how_long_the_export_took() -> None:
    send, _, _, _ = _run(_summary(modified=["a.md"]))
    assert send.call_args.args[0].text().startswith("Run complete in ")


# ── what one summary becomes on the report ──────────────────────────────


def _recorded(summary: dict) -> flow.RunReport:
    report = flow.RunReport(flow_name=flow.FLOW_NAME, repo=flow.REPO, run_id="r")
    flow.record_summary(report, summary)
    return report


def test_dropped_items_make_the_run_warn() -> None:
    report = _recorded(
        _summary(modified=["a.md"], dropped=[("missing_entity", "rel abc")])
    )
    assert report.severity == "WARN"
    assert report.issues["missing_entity"] == 1


def test_a_clean_export_still_reports_success() -> None:
    report = _recorded(_summary(modified=["a.md"]))
    assert report.severity == "SUCCESS"
    assert not report.issues


def test_source_pages_counted_only_when_they_differ_from_sources() -> None:
    report = _recorded(_summary(modified=["a.md"], sources=2, source_pages=1))
    assert report.counters["sources"] == 2
    assert report.counters["source_pages"] == 1

    assert "source_pages" not in _recorded(_summary(modified=["a.md"])).counters


# ── the concurrency slot is gone on purpose ─────────────────────────────


def test_the_export_holds_no_prefect_machinery() -> None:
    """The slot guarded one shared working tree; each run now has its own."""
    assert not hasattr(flow, "concurrency")
    assert not hasattr(flow, "get_run_logger")
    # Importing the cog must not drag Prefect in behind it.
    assert not any(m == "prefect" or m.startswith("prefect.") for m in sys.modules)


@pytest.mark.parametrize("attr", ["export_flow", "wiki_curator_router"])
def test_the_prefect_era_entrypoints_are_gone(attr: str) -> None:
    assert not hasattr(flow, attr)


# ── what the report says changed ────────────────────────────────────────
#
# The render rewrites every page every run, so "pages written" is a
# constant and cannot distinguish a run that changed the wiki from one
# that rebuilt it identically. The report has to ask git.


def test_an_identical_rebuild_records_no_outcome() -> None:
    """2505 pages rewritten, nothing different: not news, so not sent."""
    report = _recorded(_summary())
    assert report.outcomes == []
    assert report.processed == 0
    # the work still shows, as a counter rather than an outcome
    assert report.counters["rendered"] == 2505


def test_a_changed_page_is_an_update_not_a_creation() -> None:
    report = _recorded(_summary(modified=["concepts/anchor.md"]))
    assert len(report.outcomes) == 1
    assert "~ wiki page: concepts/anchor.md" in report.text()


def test_a_new_page_is_a_creation() -> None:
    report = _recorded(_summary(added=["concepts/new-thing.md"]))
    assert "+ wiki page: concepts/new-thing.md" in report.text()


def test_a_dropped_page_is_a_removal() -> None:
    report = _recorded(_summary(removed=["concepts/gone.md"]))
    assert "- wiki page: concepts/gone.md" in report.text()


def test_processed_counts_what_changed_not_what_was_rewritten() -> None:
    report = _recorded(
        _summary(added=["a.md"], modified=["b.md", "c.md"], removed=["d.md"])
    )
    assert report.processed == 4
    assert len(report.outcomes) == 4
    assert report.counters["rendered"] == 2505
