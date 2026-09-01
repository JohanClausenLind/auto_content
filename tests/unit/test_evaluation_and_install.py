from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.models.evaluation import EvalCase, EvaluationPack, apply_approval, run_pack
from content_factory.models.install import (
    ModelFileSpec,
    ModelInstallError,
    ModelInstallPlan,
    compute_actions,
    file_sha256,
    validate_plan,
)
from content_factory.schemas.skills import ExecutionLocation, ModelDescriptor, QualityFloor

PACK = EvaluationPack(
    pack_id="structured-v1",
    version="1.0.0",
    skill_id="fixture.echo",
    metric="schema_pass_rate",
    scorer="json_field_exact",
    cases=tuple(
        EvalCase(case_id=f"c{i}", input={"q": f"question {i}"}, expected={"answer": f"a{i}"})
        for i in range(10)
    ),
)
FLOOR = QualityFloor(evaluation_pack="structured-v1", metric="schema_pass_rate", minimum=0.9)


def test_strong_model_approved_weak_model_rejected() -> None:
    strong = run_pack(
        PACK,
        lambda inp: json.dumps({"answer": "a" + inp["q"].split()[-1]}),
        alias="strong",
        floor=FLOOR,
    )
    assert strong.value == 1.0 and strong.passed_floor

    def weak(inp: dict[str, str]) -> str:
        n = int(inp["q"].split()[-1])
        if n < 3:
            raise RuntimeError("crash")  # crashes score zero, never escalate themselves
        return json.dumps({"answer": f"a{n}"}) if n < 8 else "not json"

    weak_result = run_pack(PACK, weak, alias="weak", floor=FLOOR)
    assert weak_result.value == 0.5 and not weak_result.passed_floor

    catalog = [
        ModelDescriptor(
            alias=a,
            provider="ollama",
            model_id=a,
            license="Apache-2.0",
            commercial_use=True,
            location=ExecutionLocation.local_gpu,
        )
        for a in ("strong", "weak")
    ]
    catalog = apply_approval(catalog, strong)
    catalog = apply_approval(catalog, weak_result)
    by = {m.alias: m for m in catalog}
    assert by["strong"].approved_for_skills == ("fixture.echo",)
    assert by["weak"].approved_for_skills == ()
    # A later failing run revokes the approval (expiry on material change).
    catalog = apply_approval(
        catalog, run_pack(PACK, lambda _i: "broken", alias="strong", floor=FLOOR)
    )
    assert {m.alias: m for m in catalog}["strong"].approved_for_skills == ()


def test_numeric_tolerance_scorer() -> None:
    pack = PACK.model_copy(
        update={
            "scorer": "numeric_within_tolerance",
            "tolerance": 0.5,
            "cases": (EvalCase(case_id="n1", input={"q": "share"}, expected={"value": 21.0}),),
        }
    )
    assert run_pack(
        pack, lambda _i: json.dumps({"value": 21.4}), alias="m", floor=FLOOR
    ).passed_floor
    assert not run_pack(
        pack, lambda _i: json.dumps({"value": 22.0}), alias="m", floor=FLOOR
    ).passed_floor


def plan(
    url: str = "https://huggingface.co/org/repo/resolve/main/model.safetensors", sha: str = "0" * 64
) -> ModelInstallPlan:
    return ModelInstallPlan(
        plan_id="p1",
        files=(
            ModelFileSpec(
                filename="model.safetensors",
                relative_path="models/checkpoints",
                source_url=url,
                sha256=sha,
                size_bytes=100,
                license="MIT",
            ),
        ),
        total_download_bytes=100,
    )


def test_install_plan_allowlist_and_actions(tmp_path: Path) -> None:
    with pytest.raises(ModelInstallError, match="allowlisted"):
        validate_plan(plan(url="https://evil.example/model.safetensors"))
    with pytest.raises(ModelInstallError, match="https"):
        validate_plan(plan(url="http://huggingface.co/x"))
    actions = compute_actions(plan(), tmp_path)
    assert actions[0].reason == "missing"
    assert actions[0].command[:4] == ("comfy", f"--workspace={tmp_path}", "model", "download")
    dest = tmp_path / "models/checkpoints/model.safetensors"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"weights")
    good = plan(sha=file_sha256(dest))
    assert compute_actions(good, tmp_path) == []
    dest.write_bytes(b"tampered")
    assert compute_actions(good, tmp_path)[0].reason == "hash_mismatch"
