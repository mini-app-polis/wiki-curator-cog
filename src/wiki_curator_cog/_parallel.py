"""Run a model pass's calls side by side, and stop them all at once.

Each model pass (link, consolidate, generate) runs its calls on a pool. A
pass stopped by the worker's deadline must not wait for the calls already
in flight; :func:`run_parallel` is how it stops at once.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


def run_parallel(
    work: Callable[[T], object],
    items: Iterable[T],
    *,
    workers: int,
    stopped: threading.Event,
) -> None:
    """Run ``work`` on each item, ``workers`` at once; stop at once on a raise.

    Not a ``with ThreadPoolExecutor`` block: leaving one waits for every
    call already running. A pass stopped by its deadline
    (:class:`mini_app_polis.lambda_worker.RunOutOfTime`, raised on the
    calling thread) must stop at once, so its report goes out
    before Lambda ends the invocation and no further model calls are paid
    for. On any exception, ``stopped`` is set, queued items are cancelled,
    and calls already in flight, which cannot be interrupted, are left to
    finish; ``work`` checks ``stopped`` and drops what they return rather
    than recording or saving it. The job is retried and asks again.
    """
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        list(pool.map(work, items))
    except BaseException:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
