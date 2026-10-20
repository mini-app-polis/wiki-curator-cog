"""A model call still in flight when the run's deadline fires.

Each model pass runs its calls on a pool. Out of time, the pass must stop at
once: calls not yet started never reach the model, the one in flight is not
waited for, and what it returns after the deadline is not recorded or
saved. These fakes let a test hold one call open across a real deadline.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from wiki_curator_cog._deadline import DEADLINE_MARGIN_SECONDS, RunOutOfTime, deadline

#: How long a held call waits to be released. Longer than the deadline, so
#: a pass that waits for its calls takes at least this long.
HOLD_SECONDS = 5


class Held:
    """Wraps a fake LLM: every call blocks until released, then answers."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.asked = 0
        self.answered = 0
        self.release = threading.Event()
        self._lock = threading.Lock()

    def generate_json(self, **kwargs: Any) -> Any:
        with self._lock:
            self.asked += 1
        self.release.wait(timeout=HOLD_SECONDS)
        out = self.inner.generate_json(**kwargs)
        with self._lock:
            self.answered += 1
        return out


def run_past_the_deadline(llm: Held, run: Callable[[], object]) -> float:
    """Run ``run`` under a 1 s deadline; the seconds it took to raise.

    Then release the call in flight and let it finish, so the test can check
    that what it returned changed nothing.
    """
    begun = time.monotonic()
    try:
        with pytest.raises(RunOutOfTime):
            with deadline(DEADLINE_MARGIN_SECONDS + 1):
                run()
        took = time.monotonic() - begun
    finally:
        llm.release.set()
    until = time.monotonic() + HOLD_SECONDS
    while llm.answered < llm.asked and time.monotonic() < until:
        time.sleep(0.01)
    time.sleep(0.1)  # the abandoned call's bookkeeping, after its answer
    return took
