from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from content_factory.brands.hierarchy import BrandHierarchyError, BrandNode, BrandTree
from content_factory.portal.requests import (
    PortalError,
    make_portal_token,
    validate_submission,
    verify_portal_token,
)
from content_factory.services.retention import sweep_artifacts


def test_brand_hierarchy_locks_propagate_and_local_overrides_behave() -> None:
    tree = BrandTree()
    tree.add(
        BrandNode(
            "root",
            None,
            "HQ",
            tokens={"accent": "#003366", "paper": "#faf8f4"},
            locked_tokens=frozenset({"accent"}),
            policies={"disclosure": "disclose_on_ask", "approval": "solo"},
            locked_policies=frozenset({"disclosure"}),
        )
    )
    tree.add(
        BrandNode(
            "nordic",
            "root",
            "Nordic",
            tokens={"paper": "#ffffff"},
            policies={"approval": "multi_stage"},
        )
    )
    tree.add(BrandNode("stockholm", "nordic", "Stockholm", tokens={"ink": "#101318"}))
    eff = tree.effective("stockholm")
    assert eff["tokens"] == {"accent": "#003366", "paper": "#ffffff", "ink": "#101318"}
    assert eff["policies"] == {"disclosure": "disclose_on_ask", "approval": "multi_stage"}
    with pytest.raises(BrandHierarchyError, match="locked upstream"):
        tree.add(BrandNode("oslo", "nordic", "Oslo", tokens={"accent": "#ff0000"}))
    with pytest.raises(BrandHierarchyError, match="locked upstream"):
        tree.add(BrandNode("kids", "root", "Kids", policies={"disclosure": "deflect"}))
    with pytest.raises(BrandHierarchyError, match="parent"):
        tree.add(BrandNode("orphan", "nope", "Orphan"))


def test_portal_token_scope_expiry_and_submission_validation() -> None:
    token = make_portal_token(
        workspace_id="ws_client000001", secret="s3cret", ttl_seconds=60, now=1000.0
    )
    assert verify_portal_token(token, secret="s3cret", now=1030.0) == "ws_client000001"
    with pytest.raises(PortalError, match="expired"):
        verify_portal_token(token, secret="s3cret", now=1061.0)
    with pytest.raises(PortalError, match="signature"):
        verify_portal_token(token[:-3] + "abc", secret="s3cret", now=1030.0)
    with pytest.raises(PortalError, match="signature"):
        verify_portal_token(token, secret="other", now=1030.0)
    sub = validate_submission(
        {
            "topic": "Spring campaign",
            "objective": "Three posts on the launch",
            "contact": "client@example.com",
            "deadline": "2026-10-01",
        }
    )
    assert sub.deadline == "2026-10-01"
    with pytest.raises(PortalError, match="contact"):
        validate_submission({"topic": "abc", "objective": "def"})


def test_retention_sweep_honours_policy_and_legal_hold(tmp_path: Path) -> None:
    old = tmp_path / "ws_a" / "renders" / "old.bin"
    fresh = tmp_path / "ws_a" / "renders" / "fresh.bin"
    held = tmp_path / "ws_a" / "legal" / "held.bin"
    for p in (old, fresh, held):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    ninety_days = time.time() - 90 * 86400
    os.utime(old, (ninety_days, ninety_days))
    os.utime(held, (ninety_days, ninety_days))
    dry = sweep_artifacts(
        tmp_path, retention_days=30, legal_hold_prefixes=("ws_a/legal",), dry_run=True
    )
    assert dry.artifacts_deleted == 1 and dry.held == 1 and old.exists()
    keep_forever = sweep_artifacts(tmp_path, retention_days=0, dry_run=False)
    assert keep_forever.artifacts_deleted == 0
    real = sweep_artifacts(
        tmp_path, retention_days=30, legal_hold_prefixes=("ws_a/legal",), dry_run=False
    )
    assert real.artifacts_deleted == 1 and not old.exists() and fresh.exists() and held.exists()
