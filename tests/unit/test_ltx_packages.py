"""LTX-2.5 package builders: the plain i2v graph and the LTXVAddGuide keyframe variants."""

from __future__ import annotations

import pytest

from content_factory.comfyui.client import inject_parameters
from content_factory.media.ltx_packages import (
    N_GUIDER,
    N_I2V,
    N_SAMPLER,
    guide_param_names,
    ltx_i2v_guided_package,
    ltx_i2v_package,
    snap_length,
)
from content_factory.schemas.comfyui import CapabilityFlag
from content_factory.schemas.fixtures import sample_ltx_i2v_package


def test_snap_length() -> None:
    assert snap_length(73) == 73 and snap_length(96) == 97 and snap_length(1) == 9
    assert snap_length(10_000) == 257
    assert all((snap_length(n) - 1) % 8 == 0 for n in range(1, 300))


def test_base_package_matches_the_proven_fixture_shape() -> None:
    pkg = ltx_i2v_package()
    fixture = sample_ltx_i2v_package()
    assert pkg.package_id == fixture.package_id
    assert {p.name for p in pkg.parameters} == {p.name for p in fixture.parameters}
    assert pkg.expected_outputs == fixture.expected_outputs == ("16",)
    assert {n["class_type"] for n in pkg.api_workflow.values()} == {
        n["class_type"] for n in fixture.api_workflow.values()
    }
    assert CapabilityFlag.image_to_video in pkg.capabilities
    sized = ltx_i2v_package(width=1024, height=576, length=97)
    assert sized.api_workflow[N_I2V]["inputs"]["width"] == 1024
    assert sized.api_workflow[N_I2V]["inputs"]["length"] == 97


@pytest.mark.parametrize("guides", [1, 2, 3, 4])
def test_guided_package_chains_guides_and_rewires_sampler(guides: int) -> None:
    pkg = ltx_i2v_guided_package(guides, width=1024, height=576, length=97)
    assert pkg.package_id == f"ltx-2.5.i2v-guided{guides}"
    assert CapabilityFlag.keyframe_guide in pkg.capabilities
    wf = pkg.api_workflow
    guide_nodes = [nid for nid, n in wf.items() if n["class_type"] == "LTXVAddGuide"]
    assert len(guide_nodes) == guides
    # chain: LTXVImgToVideo -> guide_1 -> ... -> guide_n -> CFGGuider / SamplerCustomAdvanced
    prev = N_I2V
    for nid in sorted(guide_nodes, key=int):
        inputs = wf[nid]["inputs"]
        assert inputs["positive"] == [prev, 0] and inputs["negative"] == [prev, 1]
        assert inputs["latent"] == [prev, 2] and inputs["vae"] == ["3", 0]
        assert wf[str(int(nid) - 1)]["class_type"] == "LoadImage"
        prev = nid
    assert wf[N_GUIDER]["inputs"]["positive"] == [prev, 0]
    assert wf[N_GUIDER]["inputs"]["negative"] == [prev, 1]
    assert wf[N_SAMPLER]["inputs"]["latent_image"] == [prev, 2]
    # every guide parameter binds and injects
    params: dict[str, int | float | str] = {"prompt": "walk", "first_frame": "a.png", "seed": 1}
    for i in range(1, guides + 1):
        img, idx, strength = guide_param_names(i)
        params.update({img: f"g{i}.png", idx: 96, strength: 0.8})
    injected = inject_parameters(pkg, params)
    last_guide = wf[prev]
    assert (
        injected[prev]["inputs"]["frame_idx"] == 96 and injected[prev]["inputs"]["strength"] == 0.8
    )
    assert last_guide["inputs"]["frame_idx"] == 96  # default pins the last frame
    assert pkg.expected_outputs == ("16",)


def test_guided_package_rejects_bad_counts() -> None:
    with pytest.raises(ValueError, match="between 1 and 4"):
        ltx_i2v_guided_package(0)
    with pytest.raises(ValueError, match="between 1 and 4"):
        ltx_i2v_guided_package(5)
