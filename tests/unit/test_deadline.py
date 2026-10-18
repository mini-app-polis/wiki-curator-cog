"""The job budget: a job stops itself before Lambda kills the invocation.

The worker passes the invocation's remaining time. A job that reaches it
fails like any other — its record goes back to the queue — instead of being
cut off mid-write.
"""

from __future__ import annotations

import signal
import time

import pytest

from wiki_curator_cog._deadline import (
    DEADLINE_MARGIN_SECONDS,
    RunOutOfTime,
    deadline,
)


def test_a_run_inside_its_budget_is_untouched() -> None:
    with deadline(DEADLINE_MARGIN_SECONDS + 5):
        pass


def test_a_non_positive_budget_disables_the_guard() -> None:
    """What a local run wants: no alarm, no margin arithmetic."""
    with deadline(0):
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_a_budget_smaller_than_the_margin_refuses_to_start() -> None:
    """Better than arming an alarm that fires before the run does anything."""
    with pytest.raises(RunOutOfTime, match="no room to start"):
        with deadline(DEADLINE_MARGIN_SECONDS):
            pytest.fail("the body must not run")


def test_a_run_that_overruns_raises_inside_the_run() -> None:
    """Raised in the run, so the report is sent on the ordinary failure path."""
    with pytest.raises(RunOutOfTime, match="before the"):
        with deadline(DEADLINE_MARGIN_SECONDS + 1):
            time.sleep(3)


def test_the_alarm_is_cleared_however_the_block_ends() -> None:
    """A leaked itimer would fire during whatever ran next."""
    with pytest.raises(ValueError, match="boom"):
        with deadline(DEADLINE_MARGIN_SECONDS + 5):
            raise ValueError("boom")
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_the_previous_handler_is_restored() -> None:
    sentinel = signal.getsignal(signal.SIGALRM)
    with deadline(DEADLINE_MARGIN_SECONDS + 5):
        pass
    assert signal.getsignal(signal.SIGALRM) is sentinel
