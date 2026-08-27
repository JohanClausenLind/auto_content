"""Offline demo runner (phase 2): fixture campaign → pruned DAG → artboard PNG + short MP4 → QC →
run report. No network, no models, no credentials. Output layout follows section 8 under
``projects/<project_id>/`` (git-ignored)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from content_factory.artifacts import FilesystemArtifactStore
from content_factory.deliverables.dag_compiler import compile_dag
from content_factory.schemas.fixtures import (
    WS,
    sample_artboard_bundle,
    sample_campaign,
    sample_timeline_bundle,
)
from content_factory.video.render import render_artboard, render_timeline

REPO_ROOT = Path(__file__).resolve().parents[3]


def run_demo(
    *, quality: str = "smoke", projects_dir: Path | None = None, artifacts_dir: Path | None = None
) -> dict:
    t0 = time.monotonic()
    projects_dir = projects_dir or REPO_ROOT / "projects"
    store = FilesystemArtifactStore(artifacts_dir or REPO_ROOT / "data" / "artifacts")
    campaign = sample_campaign()
    project_id = "prj_demo00000001"
    root = projects_dir / project_id
    (root / "deliverables").mkdir(parents=True, exist_ok=True)
    dag = compile_dag(campaign)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "workspace_id": WS,
                "campaign_id": campaign.campaign_id,
                "quality": quality,
            },
            indent=1,
        )
    )
    (root / "brief.json").write_text(campaign.brief.model_dump_json(indent=1))
    (root / "dag.json").write_text(dag.model_dump_json(indent=1))

    report: dict = {
        "project_id": project_id,
        "quality": quality,
        "deliverables": {},
        "dag_nodes": len(dag.nodes),
        "dag_pruned": len(dag.pruned),
    }

    art = sample_artboard_bundle()
    assert art.artboard is not None
    art_spec = art.artboard
    ddir = root / "deliverables" / art_spec.deliverable_id
    (ddir / "artboards").mkdir(parents=True, exist_ok=True)
    (ddir / "spec.json").write_text(
        next(
            d for d in campaign.deliverables if d.deliverable_id == art_spec.deliverable_id
        ).model_dump_json(indent=1)
    )
    (ddir / "artboards" / "artboard.json").write_text(art_spec.model_dump_json(indent=1))
    outcome = render_artboard(art, workspace_id=WS, store=store, workdir=ddir / "exports")
    report["deliverables"][art_spec.deliverable_id] = {
        "type": "single_image_post",
        "artifact": asdict(outcome.artifact),
        "qc_passed": outcome.qc.passed,
        "qc": [asdict(f) for f in outcome.qc.findings],
        "facts": outcome.qc.facts,
        "renderer": outcome.renderer_stdout,
        "bundle_sha256": outcome.bundle_sha256,
    }

    tl = sample_timeline_bundle()
    assert tl.plan is not None and tl.timeline is not None
    plan, compiled = tl.plan, tl.timeline
    vdir = root / "deliverables" / plan.deliverable_id
    (vdir / "timeline").mkdir(parents=True, exist_ok=True)
    (vdir / "spec.json").write_text(
        next(
            d for d in campaign.deliverables if d.deliverable_id == plan.deliverable_id
        ).model_dump_json(indent=1)
    )
    (vdir / "timeline" / "plan.json").write_text(plan.model_dump_json(indent=1))
    (vdir / "timeline" / "compiled.json").write_text(compiled.model_dump_json(indent=1))
    scene_id = compiled.scenes[1].scene_id if quality == "smoke" else None
    outcome_v = render_timeline(
        tl, workspace_id=WS, store=store, workdir=vdir / "exports", scene_id=scene_id
    )
    report["deliverables"][plan.deliverable_id] = {
        "type": "short_video",
        "rendered_scope": scene_id or "full",
        "artifact": asdict(outcome_v.artifact),
        "qc_passed": outcome_v.qc.passed,
        "qc": [asdict(f) for f in outcome_v.qc.findings],
        "facts": outcome_v.qc.facts,
        "renderer": outcome_v.renderer_stdout,
        "bundle_sha256": outcome_v.bundle_sha256,
    }
    report["elapsed_s"] = round(time.monotonic() - t0, 1)
    report["passed"] = all(d["qc_passed"] for d in report["deliverables"].values())
    (root / "final").mkdir(exist_ok=True)
    (root / "final" / "run-report.json").write_text(json.dumps(report, indent=1, default=str))
    return report
