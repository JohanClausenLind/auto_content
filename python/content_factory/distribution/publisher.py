"""The idempotent publish service (22.4): intent → validate → policy recheck → publish once.

State machine per intent: pending → published | ambiguous | blocked | failed. A retry first
consults the stored intent, then the platform (find_existing); only proven absence publishes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from content_factory.distribution.backend import (
    AmbiguousPublishError,
    DistributionBackend,
    PostPackage,
    PublishBlockedError,
    PublishReceipt,
    PublishState,
    TransientPublishError,
)
from content_factory.logging import get_logger

log = get_logger(__name__)


@dataclass
class IntentStore:
    """File-backed intent log (Postgres-backed in the durable workflow; same shape)."""

    root: Path

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> dict | None:
        p = self._path(key)
        return json.loads(p.read_text()) if p.exists() else None

    def save(self, key: str, record: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True))
        tmp.replace(self._path(key))


@dataclass(frozen=True)
class PublishPolicy:
    kill_switch: bool
    distribution_enabled: bool
    allowed_visibilities: tuple[str, ...] = ("private", "draft", "unlisted", "public")


def publish_with_intent(
    key: str,
    package: PostPackage,
    backend: DistributionBackend,
    store: IntentStore,
    policy: PublishPolicy,
    *,
    max_attempts: int = 4,
) -> tuple[PublishState, PublishReceipt | None]:
    if policy.kill_switch:
        raise PublishBlockedError("the distribution kill switch is ON")
    if not policy.distribution_enabled:
        raise PublishBlockedError("distribution is disabled in configuration")
    if package.visibility not in policy.allowed_visibilities:
        raise PublishBlockedError(
            f"visibility {package.visibility!r} is not authorized by the active profile"
        )

    record = store.load(key)
    if record:
        state = PublishState(record["state"])
        if state == PublishState.published:
            return state, PublishReceipt(
                record["remote_id"], record.get("url"), record.get("raw", {})
            )
        if state == PublishState.ambiguous:
            existing = backend.find_existing(package)
            if existing:
                store.save(
                    key,
                    {
                        "state": "published",
                        "remote_id": existing.remote_id,
                        "url": existing.url,
                        "raw": existing.raw,
                    },
                )
                return PublishState.published, existing
            # Proven absent: fall through and publish.
    problems = backend.validate(package)
    if problems:
        store.save(key, {"state": "blocked", "problems": problems})
        raise PublishBlockedError("; ".join(problems))

    last_error = ""
    for attempt in range(1, max_attempts + 1):
        # Exactly-once discipline: before EVERY attempt after the first, reconcile remotely.
        if attempt > 1:
            existing = backend.find_existing(package)
            if existing:
                store.save(
                    key,
                    {
                        "state": "published",
                        "remote_id": existing.remote_id,
                        "url": existing.url,
                        "raw": existing.raw,
                    },
                )
                return PublishState.published, existing
        try:
            receipt = backend.publish(package)
        except TransientPublishError as exc:
            last_error = str(exc)
            log.warning(
                "publish.transient",
                attempt=attempt,
                platform=backend.platform,
                error=last_error[:200],
            )
            continue
        except AmbiguousPublishError as exc:
            store.save(key, {"state": "ambiguous", "error": str(exc)[:500]})
            existing = backend.find_existing(package)
            if existing:
                store.save(
                    key,
                    {
                        "state": "published",
                        "remote_id": existing.remote_id,
                        "url": existing.url,
                        "raw": existing.raw,
                    },
                )
                return PublishState.published, existing
            return (
                PublishState.ambiguous,
                None,
            )  # blocking reconciliation state; a later run resolves
        store.save(
            key,
            {
                "state": "published",
                "remote_id": receipt.remote_id,
                "url": receipt.url,
                "raw": receipt.raw,
            },
        )
        return PublishState.published, receipt
    store.save(key, {"state": "failed", "error": last_error[:500]})
    return PublishState.failed, None
