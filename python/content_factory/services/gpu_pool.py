"""Spread independent GPU items over more than one model server."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor


class WorkerPool[W]:
    """Interchangeable workers, one item in flight each."""

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
        """Run ``fn(worker, item)`` for every item, returning results in ``items`` order."""
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
