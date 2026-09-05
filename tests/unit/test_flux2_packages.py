"""FLUX.2-dev reference package: the ReferenceLatent chain, its cap, and the turbo/full presets.

`image_sequences.backend` can be set to `flux2`, so this package is a production path. What makes
it different from the LTX and Wan packages next door is that conditioning is *chained*: every
reference wraps the previous one, and FluxGuidance has to end up after the whole chain rather than
on the bare text, or the composed references are guided as if they were not there.
"""

from __future__ import annotations

import pytest

from content_factory.comfyui.client import inject_parameters
from content_factory.media.flux2_packages import (
    FULL_STEPS,
    GUIDANCE,
    MAX_REFERENCES,
    N_GUIDANCE,
    N_POS,
    TRANSFORMER,
    TURBO_GUIDANCE,
    TURBO_LORA,
    TURBO_STEPS,
    VAE,
    flux2_reference_package,
    reference_node_ids,
    reference_param_name,
)
from content_factory.schemas.comfyui import CapabilityFlag


def test_no_references_is_plain_text_to_image() -> None:
    pkg = flux2_reference_package(0)
    assert pkg.package_id == "flux2-dev.t2i"
    assert CapabilityFlag.text_to_image in pkg.capabilities
    # Nothing to compose, so it must not advertise composition.
    assert CapabilityFlag.reference_image_edit not in pkg.capabilities
    assert not [p for p in pkg.parameters if p.name.startswith("reference_")]
    # Guidance sits directly on the text conditioning.
    assert pkg.api_workflow[N_GUIDANCE]["inputs"]["conditioning"] == [N_POS, 0]


def test_references_chain_and_guidance_moves_to_the_end_of_the_chain() -> None:
    refs = 3
    pkg = flux2_reference_package(refs)
    wf = pkg.api_workflow
    assert pkg.package_id == f"flux2-dev.reference-{refs}"

    previous: list[object] = [N_POS, 0]
    for i in range(1, refs + 1):
        load_id, encode_id, ref_id = reference_node_ids(i)
        assert wf[load_id]["class_type"] == "LoadImage"
        assert wf[encode_id]["class_type"] == "VAEEncode"
        assert wf[encode_id]["inputs"]["pixels"] == [load_id, 0]
        assert wf[ref_id]["class_type"] == "ReferenceLatent"
        # Each reference wraps the one before it, in upload order.
        assert wf[ref_id]["inputs"]["conditioning"] == previous
        assert wf[ref_id]["inputs"]["latent"] == [encode_id, 0]
        previous = [ref_id, 0]

    # The whole point: guidance applies to the composed conditioning, not to the bare text.
    last_ref = reference_node_ids(refs)[2]
    assert wf[N_GUIDANCE]["inputs"]["conditioning"] == [last_ref, 0]

    assert CapabilityFlag.reference_image_edit in pkg.capabilities


def test_reference_node_ids_never_renumber_the_base_graph() -> None:
    base_ids = set(flux2_reference_package(0).api_workflow)
    for i in range(1, MAX_REFERENCES + 1):
        assert not base_ids & set(reference_node_ids(i)), f"reference {i} collides with the base"
    # And the triples do not collide with each other.
    allocated = [nid for i in range(1, MAX_REFERENCES + 1) for nid in reference_node_ids(i)]
    assert len(allocated) == len(set(allocated))


def test_the_reference_cap_is_enforced_rather_than_silently_truncated() -> None:
    flux2_reference_package(MAX_REFERENCES)  # the cap itself is allowed
    for bad in (-1, MAX_REFERENCES + 1):
        with pytest.raises(ValueError, match="references must be between"):
            flux2_reference_package(bad)


def test_turbo_and_full_carry_their_own_steps_guidance_and_lora() -> None:
    turbo = flux2_reference_package(1, turbo=True)
    full = flux2_reference_package(1, turbo=False)

    turbo_loras = {m.filename for m in turbo.required_models}
    assert TURBO_LORA in turbo_loras
    assert TURBO_LORA not in {m.filename for m in full.required_models}

    # The distilled LoRA is trained at low guidance; the bare model documents 4.0.
    assert turbo.api_workflow[N_GUIDANCE]["inputs"]["guidance"] == TURBO_GUIDANCE
    assert full.api_workflow[N_GUIDANCE]["inputs"]["guidance"] == GUIDANCE
    assert TURBO_STEPS < FULL_STEPS

    # Explicit overrides win over both presets.
    pinned = flux2_reference_package(1, turbo=True, steps=11, guidance=2.5)
    assert pinned.api_workflow[N_GUIDANCE]["inputs"]["guidance"] == 2.5


def test_every_package_declares_the_weights_it_loads() -> None:
    names = {m.filename for m in flux2_reference_package(2).required_models}
    assert {TRANSFORMER, VAE} <= names


def test_uploaded_filenames_bind_to_their_own_load_image_nodes() -> None:
    refs = 2
    pkg = flux2_reference_package(refs)
    names = {p.name for p in pkg.parameters}
    assert {"prompt", "seed", reference_param_name(1), reference_param_name(2)} <= names

    wf = inject_parameters(
        pkg,
        {
            "prompt": "the same character, new coat",
            "seed": 7,
            reference_param_name(1): "identity.png",
            reference_param_name(2): "coat.png",
        },
    )
    assert wf[reference_node_ids(1)[0]]["inputs"]["image"] == "identity.png"
    assert wf[reference_node_ids(2)[0]]["inputs"]["image"] == "coat.png"
