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
import threading
import time
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

        An item that fails on one worker is **retried on the others** before its failure counts.
        A GPU host is a separate machine that can be rebooted, preempted by another tenant, or
        simply die: measured on this pipeline, a HiDream server holding 19 GB was killed when
        another program on the same card asked Ollama for a model, and the sequence stage failed
        with "server unreachable" seventeen minutes in, with a second healthy host idle beside it.
        A pool whose whole purpose is that there is more than one card should not lose a run to
        one of them going away.

        A worker that has failed is skipped while any worker has not, so the rest of the run does
        not pay a connection error per item for a host that is gone. It is never removed for good:
        the flag is per call, and a pool where every worker has failed stops skipping and lets the
        real exception out, so a bad *item* still fails rather than looking like eight dead hosts.

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
        failed: set[int] = set()
        guard = threading.Lock()

        def _run(item: I) -> R:
            last: BaseException | None = None
            # Enough turns to skip every flagged worker and still try every live one.
            for _turn in range(2 * len(self._workers) + 1):
                worker = free.get()
                try:
                    with guard:
                        skip = id(worker) in failed and len(failed) < len(self._workers)
                    if skip:
                        time.sleep(0.01)
                        continue
                    return fn(worker, item)
                except Exception as exc:
                    last = exc
                    with guard:
                        failed.add(id(worker))
                finally:
                    free.put(worker)
            if last is not None:
                raise last
            msg = "every worker in the pool has failed"
            raise RuntimeError(msg)

        with ThreadPoolExecutor(
            max_workers=len(self._workers), thread_name_prefix="gpu-pool"
        ) as pool:
            return list(pool.map(_run, items))
