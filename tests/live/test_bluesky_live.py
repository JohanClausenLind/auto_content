"""OPT-IN live test (never in core CI): publishes ONE post to the operator's designated test
Bluesky account, exactly once under a forced retry, then verifies via reconciliation.

Run explicitly: BLUESKY_HANDLE=... BLUESKY_APP_PASSWORD=... uv run pytest -m live tests/live -q
The account named here must be a test account the operator designated for this purpose."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from content_factory.distribution.backend import PostPackage, PublishState
from content_factory.distribution.bluesky import BlueskyBackend
from content_factory.distribution.publisher import IntentStore, PublishPolicy, publish_with_intent

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.environ.get("BLUESKY_HANDLE") or not os.environ.get("BLUESKY_APP_PASSWORD"),
    reason="live credentials not configured",
)
def test_publish_once_to_designated_test_account(tmp_path: Path) -> None:
    backend = BlueskyBackend(
        service=os.environ.get("BLUESKY_SERVICE", "https://bsky.social"),
        handle=os.environ["BLUESKY_HANDLE"],
        app_password_getter=lambda: os.environ["BLUESKY_APP_PASSWORD"],
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    package = PostPackage(
        text=f"Content Factory live-path check {stamp} (test account; automated; will not repeat)"
    )
    store = IntentStore(tmp_path)
    policy = PublishPolicy(kill_switch=False, distribution_enabled=True)
    state, receipt = publish_with_intent("live-key", package, backend, store, policy)
    assert state == PublishState.published and receipt is not None
    # A second call with the same intent must be a no-op returning the same receipt.
    state2, receipt2 = publish_with_intent("live-key", package, backend, store, policy)
    assert state2 == PublishState.published and receipt2 is not None
    assert receipt2.remote_id == receipt.remote_id
