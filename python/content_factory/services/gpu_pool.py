"""Spread independent GPU items over more than one model server.

The pipeline's expensive work is a list of items that do not depend on each other -- the frames of
a hub-and-spoke sequence, the clips of a shot list -- and each one costs minutes on a card. Until
now they ran one at a time against a single loopback endpoint, so a second machine could not help.

**One in-flight item per worker, and parallelism comes from having more than one worker.** The
model servers each own a whole card and serialise internally (``skills/image/hidream/server.py``
holds a ``threading.Lock`` around generation; ComfyUI has its own prompt queue), so firing two
requests at the same endpoint would queue them, not overlap them, while costing the scheduler the
ability to see which host is free.

Assignment is dynamic rather than round-robin: a frame that fails its drift check is regenerated up
to three times, so items differ in cost by a factor of three and a static split would leave one
card idle. Order is preserved on the way out, because the caller's records and the sequence's
frame list are position-sensitive.
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor


class WorkerPool[W]:
    """Interchangeable workers, one item in flight each.

    A single worker is not merely a pool of one: it takes the serial path with no threads at all,
    so a run against one endpoint executes exactly as it did before this module existed. That is
    what keeps the byte-identical output the sequence tests pin.
    """

    def __init__(self, workers: Sequence[W]) -> None:
        if not workers:
            msg = "WorkerPool needs at least one worker"
            raise ValueError(msg)
        self._workers = tuple(workers)

    def __len__(self) -> int:
        return len(self._workers)

    @property
    def workers(self) -> tuple[W, ...]:
        return self._workers

    def map_ordered[I, R](self, items: Sequence[I], fn: Callable[[W, I], R]) -> list[R]:
        """Run ``fn(worker, item)`` for every item, returning results in ``items`` order.

        The first failure in item order propagates, matching the serial loop this replaces. Items
        already dispatched to other workers still finish -- their GPU time is spent either way, and
        cancelling mid-generation is what leaves a card in a bad state.
        """
        if len(self._workers) == 1 or len(items) <= 1:
            worker = self._workers[0]
            return [fn(worker, item) for item in items]

        free: queue.Queue[W] = queue.Queue()
        for worker in self._workers:
            free.put(worker)

        def _run(item: I) -> R:
            worker = free.get()
            try:
                return fn(worker, item)
            finally:
                free.put(worker)

        with ThreadPoolExecutor(
            max_workers=len(self._workers), thread_name_prefix="gpu-pool"
        ) as pool:
            return list(pool.map(_run, items))
