"""Skill registry signing/lifecycle and routing policy semantics (18.1, 18.4, 18.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from content_factory.hardware.probe import mock_inventory
from content_factory.models.routing import decide
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutionPolicy,
    ExecutorType,
    Lifecycle,
    ModelDescriptor,
    PolicyKind,
    QualityFloor,
    SkillManifest,
)
from content_factory.skills.registry import SignatureError, SkillRegistry, sign_manifest

KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()


def manifest(**over) -> SkillManifest:
    base = dict(
        skill_id="fixture.echo",
        version="1.0.0",
        status=Lifecycle.active,
        purpose="echo input (fixture)",
        input_schema="EditBatch",
        output_schema="EditBatch",
        executor=ExecutorType.model_role,
        implementation_ref="content_factory.skills.fixtures:echo",
        permitted_locations=(
            ExecutionLocation.local_gpu,
            ExecutionLocation.local_cpu,
            ExecutionLocation.cloud,
        ),
        required_models=("fast_structured", "fast_structured_cloud", "weak_local", "big_local"),
        license_evidence="Apache-2.0 (this repository)",
        cost=CostEstimator(kind="fixed", usd=0),
        timeout_seconds=30,
        max_retries=1,
        quality_floor=QualityFloor(
            evaluation_pack="structured-v1", metric="schema_pass_rate", minimum=0.95
        ),
    )
    base.update(over)
    return SkillManifest(**base)  # type: ignore[arg-type]


CATALOG = [
    ModelDescriptor(
        alias="fast_structured",
        provider="ollama",
        model_id="qwen3:8b",
        license="Apache-2.0",
        commercial_use=True,
        location=ExecutionLocation.local_gpu,
        vram_bytes_estimate=6 * 1024**3,
        approved_for_skills=("fixture.echo",),
    ),
    ModelDescriptor(
        alias="weak_local",
        provider="ollama",
        model_id="tiny:1b",
        license="Apache-2.0",
        commercial_use=True,
        location=ExecutionLocation.local_gpu,
        vram_bytes_estimate=1 * 1024**3,
        approved_for_skills=(),
    ),
    ModelDescriptor(
        alias="big_local",
        provider="vllm",
        model_id="huge:70b",
        license="Apache-2.0",
        commercial_use=True,
        location=ExecutionLocation.local_gpu,
        vram_bytes_estimate=40 * 1024**3,
        approved_for_skills=("fixture.echo",),
    ),
    ModelDescriptor(
        alias="fast_structured_cloud",
        provider="anthropic",
        model_id="claude-haiku-4-5-20251001",
        license="commercial",
        commercial_use=True,
        location=ExecutionLocation.cloud,
        usd_per_million_input_tokens=1.0,
        usd_per_million_output_tokens=5.0,
        approved_for_skills=("fixture.echo",),
    ),
]


def test_registry_refuses_unsigned_or_foreign_signatures(tmp_path: Path) -> None:
    reg = SkillRegistry(trusted_keys=(KEY.public_key(),))
    with pytest.raises(SignatureError):
        reg.register(manifest())
    with pytest.raises(SignatureError):
        reg.register(sign_manifest(manifest(), OTHER))
    signed = sign_manifest(manifest(), KEY)
    reg.register(signed)
    tampered = signed.model_copy(update={"purpose": "exfiltrate everything"})
    with pytest.raises(SignatureError):
        reg.register(tampered.model_copy(update={"version": "1.0.1"}))
    # Directory load: one good, one tampered → one loaded, one rejected with reason.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "manifest.json").write_text(
        json.dumps(sign_manifest(manifest(version="2.0.0"), KEY).model_dump(mode="json"))
    )
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(tampered.model_dump(mode="json")))
    reg2 = SkillRegistry(trusted_keys=(KEY.public_key(),))
    assert reg2.load_dir(tmp_path) == 1
    assert len(reg2.rejected) == 1


def test_lifecycle_governs_selectability() -> None:
    reg = SkillRegistry(trusted_keys=(KEY.public_key(),))
    for v, st in [
        ("1.0.0", Lifecycle.deprecated),
        ("1.1.0", Lifecycle.active),
        ("1.2.0", Lifecycle.canary),
        ("1.3.0", Lifecycle.draft),
        ("0.9.0", Lifecycle.quarantined),
    ]:
        reg.register(sign_manifest(manifest(version=v, status=st), KEY))
    assert [m.version for m in reg.selectable("fixture.echo")] == ["1.1.0", "1.2.0"]


def test_local_only_makes_zero_cloud_choices_and_pauses_when_local_is_unfit() -> None:
    d = decide(
        manifest(), PRESETS["offline"], CATALOG, mock_inventory("rtx3090"), budget_remaining_usd=100
    )
    assert d.outcome == "dispatch" and d.chosen_alias == "fast_structured"
    assert all(r.alias != "fast_structured_cloud" or "local_only" in r.reason for r in d.rejected)
    # An 8 GB GPU still fits the 6 GiB approved model (12% headroom) → dispatch locally.
    mid = decide(
        manifest(),
        PRESETS["offline"],
        CATALOG,
        mock_inventory("rtx4060_8gb"),
        budget_remaining_usd=100,
    )
    assert mid.outcome == "dispatch" and mid.chosen_alias == "fast_structured"
    # Without a GPU nothing approved fits; local_only must PAUSE — never redefine quality
    # (weak_local is rejected by the floor even though it would run) and never reach the cloud.
    none = decide(
        manifest(),
        PRESETS["offline"],
        CATALOG,
        mock_inventory("cpu_only"),
        budget_remaining_usd=100,
    )
    assert none.outcome == "pause_insufficient_quality"
    assert none.chosen_alias is None
    assert any(r.alias == "weak_local" and "quality floor" in r.reason for r in none.rejected)
    assert any(
        r.alias == "fast_structured_cloud" and "local_only" in r.reason for r in none.rejected
    )


def test_weak_model_that_fits_vram_is_rejected_by_quality_floor() -> None:
    d = decide(
        manifest(),
        PRESETS["balanced"],
        CATALOG,
        mock_inventory("rtx3090"),
        budget_remaining_usd=100,
    )
    assert d.chosen_alias == "fast_structured"
    assert any(r.alias == "weak_local" and "quality floor" in r.reason for r in d.rejected)
    assert any(r.alias == "big_local" and "VRAM" in r.reason for r in d.rejected)


def test_prefer_local_falls_back_to_cloud_visibly_and_respects_caps() -> None:
    d = decide(
        manifest(),
        PRESETS["economy"],
        CATALOG,
        mock_inventory("cpu_only"),
        budget_remaining_usd=100,
    )
    assert (
        d.outcome == "dispatch"
        and d.chosen_alias == "fast_structured_cloud"
        and d.fallback_used is True
    )
    capped = ExecutionPolicy(
        kind=PolicyKind.prefer_local, allow_cloud_fallback=True, max_external_usd=0.000001
    )
    d2 = decide(manifest(), capped, CATALOG, mock_inventory("cpu_only"), budget_remaining_usd=100)
    assert d2.outcome == "pause_budget" and d2.chosen_alias is None
    d3 = decide(
        manifest(),
        PRESETS["economy"],
        CATALOG,
        mock_inventory("cpu_only"),
        budget_remaining_usd=0.0,
    )
    assert d3.outcome == "pause_budget"


def test_pinned_policy_fails_closed_when_pin_is_unavailable() -> None:
    pinned = ExecutionPolicy(
        kind=PolicyKind.pinned, pinned_alias="big_local", allow_cloud_fallback=False
    )
    d = decide(manifest(), pinned, CATALOG, mock_inventory("rtx3090"), budget_remaining_usd=100)
    assert d.outcome != "dispatch" and d.chosen_alias is None
    with pytest.raises(ValueError, match="pinned_alias"):
        ExecutionPolicy(kind=PolicyKind.pinned)
    with pytest.raises(ValueError, match="local_only"):
        ExecutionPolicy(kind=PolicyKind.local_only, allow_cloud_fallback=True)


def test_real_probe_produces_valid_inventory() -> None:
    from content_factory.hardware.probe import probe_hardware

    inv = probe_hardware()
    assert inv.cpu_threads >= 1 and inv.ram_bytes > 0
