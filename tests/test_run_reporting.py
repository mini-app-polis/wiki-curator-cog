"""The cog reports its own run outcome as a notification.

Findings this cog posts through :mod:`wiki_curator_cog.api_client` are
graded results and stay rows. This is the separate question of whether
the export itself ran, which is not a finding and is not persisted.
"""

from __future__ import annotations

from unittest.mock import patch

import wiki_curator_cog.main as main


def _summary(
    written: int = 0,
    removed: int = 0,
    *,
    sources: int = 3,
    source_pages: int | None = None,
    dropped: list[tuple[str, str]] | None = None,
) -> dict:
    out = {
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
    return out


def test_export_that_changed_the_wiki_is_notable() -> None:
    with (
        patch.object(main, "export_flow", return_value=_summary(written=4)),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        result = main.wiki_curator_router.fn()

    assert result["paths_written"] == 4
    send.assert_called_once()
    report = send.call_args.args[0]
    assert report.repo == "wiki-curator-cog"
    assert report.severity == "SUCCESS"
    assert report.processed == 4
    # Notability is no longer hand-set: four pages moved, so the run has
    # four outcomes, and the library sends a SUCCESS that has any.
    assert len(report.outcomes) == 4
    assert "+ wiki page: entities/e0.md" in report.text()


def test_export_that_changed_nothing_is_not_notable() -> None:
    """Re-rendering the same bundle is the steady state, not news."""
    with (
        patch.object(main, "export_flow", return_value=_summary()),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert report.outcomes == []
    assert not send.call_args.kwargs.get("notable")


def test_removals_alone_count_as_a_change() -> None:
    with (
        patch.object(main, "export_flow", return_value=_summary(removed=2)),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert len(report.outcomes) == 2
    assert "- wiki page: sources/s0.md" in report.text()


def test_a_summary_without_paths_still_says_the_wiki_changed() -> None:
    """An older export shape reports counts and no paths."""
    summary = _summary(written=3)
    del summary["written_paths"]
    with (
        patch.object(main, "export_flow", return_value=summary),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert len(report.outcomes) == 3
    assert "+ wiki page x3" in report.text()


def test_the_report_says_how_long_the_export_took() -> None:
    with (
        patch.object(main, "export_flow", return_value=_summary(written=1)),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    assert send.call_args.args[0].text().startswith("Run complete in ")


def test_a_clean_export_still_reports_success() -> None:
    """No drops, no collisions — severity unchanged from today."""
    with (
        patch.object(main, "export_flow", return_value=_summary(written=1)),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert report.severity == "SUCCESS"
    assert not report.issues


def test_dropped_items_make_the_run_warn() -> None:
    dropped = [("missing_entity", "relation abc from")]
    with (
        patch.object(
            main,
            "export_flow",
            return_value=_summary(written=1, dropped=dropped),
        ),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert report.severity == "WARN"
    assert report.issues["missing_entity"] == 1


def test_source_pages_counted_when_they_differ_from_sources() -> None:
    with (
        patch.object(
            main,
            "export_flow",
            return_value=_summary(written=1, sources=2, source_pages=1),
        ),
        patch.object(main.RunReport, "send", autospec=True) as send,
    ):
        main.wiki_curator_router.fn()

    report = send.call_args.args[0]
    assert report.counters["sources"] == 2
    assert report.counters["source_pages"] == 1


def test_flow_declares_failure_hooks() -> None:
    """A crash must reach the channel even though nothing else reports it."""
    flow = main.wiki_curator_router
    assert flow.on_failure_hooks
    assert flow.on_crashed_hooks
