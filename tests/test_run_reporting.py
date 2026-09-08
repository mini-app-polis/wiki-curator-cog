"""The cog reports its own run outcome as a notification.

Findings this cog posts through :mod:`wiki_curator_cog.api_client` are
graded results and stay rows. This is the separate question of whether
the export itself ran, which is not a finding and is not persisted.
"""

from __future__ import annotations

from unittest.mock import patch

import wiki_curator_cog.main as main


def _summary(written: int = 0, removed: int = 0) -> dict:
    return {
        "entities": 12,
        "sources": 3,
        "instructors": 2,
        "paths_written": written,
        "paths_removed": removed,
    }


def test_export_that_changed_the_wiki_is_notable() -> None:
    with (
        patch.object(main, "export_flow", return_value=_summary(written=4)),
        patch.object(main, "post_run_finding") as post,
    ):
        result = main.wiki_curator_router.fn()

    assert result["paths_written"] == 4
    post.assert_called_once()
    assert post.call_args.kwargs["notable"] is True
    assert post.call_args.kwargs["repo"] == "wiki-curator-cog"
    assert "4 written" in post.call_args.kwargs["text"]


def test_export_that_changed_nothing_is_not_notable() -> None:
    """Re-rendering the same bundle is the steady state, not news."""
    with (
        patch.object(main, "export_flow", return_value=_summary()),
        patch.object(main, "post_run_finding") as post,
    ):
        main.wiki_curator_router.fn()

    assert post.call_args.kwargs["notable"] is False


def test_removals_alone_count_as_a_change() -> None:
    with (
        patch.object(main, "export_flow", return_value=_summary(removed=2)),
        patch.object(main, "post_run_finding") as post,
    ):
        main.wiki_curator_router.fn()

    assert post.call_args.kwargs["notable"] is True


def test_flow_declares_failure_hooks() -> None:
    """A crash must reach the channel even though nothing else reports it."""
    flow = main.wiki_curator_router
    assert flow.on_failure_hooks
    assert flow.on_crashed_hooks
