"""Temporal worker entry point by resource class (task queue). Grows per phase."""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from content_factory.config import get_settings
from content_factory.logging import configure_logging, get_logger
from content_factory.workflows import spike

log = get_logger(__name__)

# task queue -> (workflows, activities). Only queues with registered work are startable.
REGISTRY: dict[str, tuple[list[type], list[object]]] = {
    "control": ([spike.SpikeProductionRun], [spike.run_stage]),
}


async def run_worker(queue: str) -> None:
    if queue not in REGISTRY:
        msg = f"unknown task queue {queue!r}; known: {sorted(REGISTRY)}"
        raise SystemExit(msg)
    settings = get_settings()
    configure_logging(json=settings.environment == "production")
    client = await Client.connect(settings.temporal.address, namespace=settings.temporal.namespace)
    workflows, activities = REGISTRY[queue]
    log.info("worker.start", queue=queue, workflows=[w.__name__ for w in workflows])
    async with Worker(client, task_queue=queue, workflows=workflows, activities=activities):  # type: ignore[arg-type]
        await asyncio.Event().wait()


def main(queue: str = "control") -> None:
    asyncio.run(run_worker(queue))
