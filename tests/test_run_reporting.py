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
    written: int = 0,
    removed: int = 0,
    *,
    sources: int = 3,
    source_pages: int | None = None,
    dropped: list[tuple[str, str]] | None = None,
) -> dict:
    return {
        "entities": 12,
        "sources": sources,
        "instructors": 2,
        "paths_written": written,
        "paths_removed": removed,
        "written_paths": [f"entities/e{i}.md" for i in range(written)],
        "removed_paths": [f"sources/s{i}.md" for i in range(removed)],
        "source_pages": sources if source_pages is None else source_pages,
        "dropped": [] if dropped is None else dropped,
    }


def _config(budget: int = 0) -> SimpleNamespace:
    """A config stub. Budget 0 disables the deadline, which suits a test."""
    return SimpleNamespace(run_timeout_seconds=budget)


def _run(summary_or_exc, *, budget: int = 0):
    """Run export_run with _export stubbed, capturing the sent report."""
    kwargs = (
        {"side_effect": summary_or_exc}
        if isinstance(summary_or_exc, BaseException)
        else {"return_value": summary_or_exc}
    )
    with (
        patch.object(flow, "load_config", return_value=_config(budget)),
        patch.object(flow, "_export", **kwargs),
        patch.object(flow.RunReport, "send", autospec=True) as send,
    ):
        try:
            result = flow.export_run(run_id="run-1")
        except BaseException as exc:  # noqa: BLE001 — the test inspects it
            return send, None, exc
    return send, result, None


# ── the report is sent, once, either way ────────────────────────────────


def test_a_successful_run_sends_one_report() -> None:
    send, result, exc = _run(_summary(written=4))

    assert exc is None
    assert result["paths_written"] == 4
    send.assert_called_once()
    report = send.call_args.args[0]
    assert report.repo == "wiki-curator-cog"
    assert report.run_id == "run-1"
    assert report.severity == "SUCCESS"
    assert report.processed == 4


def test_a_failed_run_still_sends_a_report_and_re_raises() -> None:
    """PIPE-021. Nothing else reports this run — there is no hook left."""
    send, _, exc = _run(RuntimeError("push rejected"))

    assert isinstance(exc, RuntimeError)
    send.assert_called_once()
    report = send.call_args.args[0]
    assert report.severity == "WARN"
    assert report.issues["unhandled_exception"] == 1


def test_a_run_that_outlives_its_budget_is_reported_not_hung() -> None:
    """The deadline failure travels the ordinary path, so Railway is freed."""
    send, _, exc = _run(RunOutOfTime("stopped early"))

    assert isinstance(exc, RunOutOfTime)
    send.assert_called_once()
    assert send.call_args.args[0].issues["unhandled_exception"] == 1


def test_the_report_carries_the_run_id_it_was_given() -> None:
    """The library's fallback resolves Prefect ids and answers 'local-run'."""
    send, _, _ = _run(_summary(written=1))
    assert send.call_args.args[0].run_id == "run-1"


def test_the_report_says_how_long_the_export_took() -> None:
    send, _, _ = _run(_summary(written=1))
    assert send.call_args.args[0].text().startswith("Run complete in ")


# ── what one summary becomes on the report ──────────────────────────────


def _recorded(summary: dict) -> flow.RunReport:
    report = flow.RunReport(flow_name=flow.FLOW_NAME, repo=flow.REPO, run_id="r")
    flow.record_summary(report, summary)
    return report


def test_pages_written_become_named_outcomes() -> None:
    report = _recorded(_summary(written=4))
    assert report.processed == 4
    assert len(report.outcomes) == 4
    assert "+ wiki page: entities/e0.md" in report.text()


def test_an_export_that_changed_nothing_records_no_outcome() -> None:
    """Re-rendering the same bundle is the steady state, not news."""
    assert _recorded(_summary()).outcomes == []


def test_removals_alone_count_as_a_change() -> None:
    report = _recorded(_summary(removed=2))
    assert len(report.outcomes) == 2
    assert "- wiki page: sources/s0.md" in report.text()


def test_a_summary_without_paths_still_says_the_wiki_changed() -> None:
    """An older export shape reports counts and no paths."""
    summary = _summary(written=3)
    del summary["written_paths"]
    report = _recorded(summary)
    assert len(report.outcomes) == 3
    assert "+ wiki page x3" in report.text()


def test_dropped_items_make_the_run_warn() -> None:
    report = _recorded(_summary(written=1, dropped=[("missing_entity", "rel abc")]))
    assert report.severity == "WARN"
    assert report.issues["missing_entity"] == 1


def test_a_clean_export_still_reports_success() -> None:
    report = _recorded(_summary(written=1))
    assert report.severity == "SUCCESS"
    assert not report.issues


def test_source_pages_counted_only_when_they_differ_from_sources() -> None:
    report = _recorded(_summary(written=1, sources=2, source_pages=1))
    assert report.counters["sources"] == 2
    assert report.counters["source_pages"] == 1

    assert "source_pages" not in _recorded(_summary(written=1)).counters


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
