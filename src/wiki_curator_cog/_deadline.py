"""Stop a job before Lambda does.

The wiki-jobs worker passes the invocation's remaining time (from
``get_remaining_time_in_millis``) as the budget. A job that reaches it
raises :class:`RunOutOfTime` inside the job, a margin before Lambda would
kill the invocation outright, so it travels the ordinary failure path: the
record is returned to the queue and retried, rather than cut off
mid-write. A non-positive budget — a local command — disables the guard.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager

#: How long before the budget a job is stopped, so that it can still fail
#: cleanly before Lambda ends the invocation.
DEADLINE_MARGIN_SECONDS = 30


class RunOutOfTime(Exception):  # noqa: N818 — named for what happened
    """The run reached its time budget and was stopped deliberately.

    Distinct from a job that failed on its own: nothing is known to be
    wrong except that it took too long. It is raised inside the job so it
    fails like any other, rather than being killed mid-write.
    """


@contextmanager
def deadline(budget_seconds: int) -> Iterator[None]:
    """Raise :class:`RunOutOfTime` a margin before ``budget_seconds``.

    SIGALRM, because the job is blocking I/O on the main thread — the
    export GET, the state reads and writes, the model calls — which is
    where the signal is delivered. A non-positive budget disables the
    guard, which is what a local run wants.
    """
    if budget_seconds <= 0:
        yield
        return

    seconds = budget_seconds - DEADLINE_MARGIN_SECONDS
    if seconds <= 0:
        raise RunOutOfTime(
            f"the run budget of {budget_seconds}s leaves no room to start"
        )

    def _expire(signum: int, frame: object) -> None:  # noqa: ARG001
        raise RunOutOfTime(
            f"stopped {DEADLINE_MARGIN_SECONDS}s before the {budget_seconds}s "
            "run budget"
        )

    previous = signal.signal(signal.SIGALRM, _expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
