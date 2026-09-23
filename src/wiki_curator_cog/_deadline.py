"""Stop a run before it outlives the thing that started it.

transcription-cog's ``_deadline.py`` reads Lambda's own clock: the
runtime hands the handler ``get_remaining_time_in_millis`` and kills the
invocation outright when it reaches zero. This cog has no runtime
clock — it is a one-shot process on Railway — so the budget is declared
rather than discovered, and the reason for having one is different.

Railway skips a scheduled or repeated start while the previous one is
still ``Active``. A run that hangs therefore does not merely take a long
time: every later start is silently skipped, the service sits ``Active``,
and nothing says so. That is the failure this guards. A run that reaches
its budget raises :class:`RunOutOfTime` inside the run, so it travels the
ordinary failure path — the flow reports it and re-raises, ``main`` exits
non-zero, and the next start is free to happen.

The margin exists for the same reason as transcription-cog's: the report
is one POST with the API client's own timeout, and a budget spent to the
last second leaves no room to send it.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager

#: How long before the declared budget a run is stopped, so that it can
#: still send its report before the process ends.
DEADLINE_MARGIN_SECONDS = 30


class RunOutOfTime(Exception):  # noqa: N818 — named for what happened
    """The run reached its time budget and was stopped deliberately.

    Distinct from a run that failed on its own: nothing is known to be
    wrong except that it took too long. It is raised inside the run so
    the flow reports it like any other failure, rather than leaving a
    process that never ends and a service that never starts again.
    """


@contextmanager
def deadline(budget_seconds: int) -> Iterator[None]:
    """Raise :class:`RunOutOfTime` a margin before ``budget_seconds``.

    SIGALRM, because the run is blocking I/O on the main thread — the
    export GET, the clone, the push — which is where the signal is
    delivered. A non-positive budget disables the guard, which is what a
    local run wants.

    The git subprocess: a push interrupted this way leaves GitPython's
    child running until the process exits. That is acceptable here and
    only here, because the process does exit — immediately, and by
    design. It would not be acceptable on a runtime that reuses the
    container.
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
