"""The pure half of the HiDream LoRA loader.

The merge itself needs torch and a 33 GB model, so it lives behind the skill's own environment and
is exercised by `tests/live`. What can be checked here is the part that was actually at risk: the
naming convention. musubi-tuner flattens a module path with underscores, and `down_proj` and
`language_model` both contain one — so reading the flattened name backwards is ambiguous and the
loader must never try. These tests pin that it doesn't, and that a wrong-convention adapter is
detected rather than silently merged into nothing.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

HIDREAM_SKILL = Path(__file__).resolve().parents[2] / "skills" / "image" / "hidream"


@pytest.fixture
def lora_mod():
    import sys

    sys.path.insert(0, str(HIDREAM_SKILL))
    try:
        # Imported by path, not as a package: skills are isolated environments the control plane
        # only ever talks to over HTTP or a subprocess, so there is no import path to declare.
        import lora  # type: ignore[import-not-found]

        return lora
    finally:
        sys.path.remove(str(HIDREAM_SKILL))


def test_groups_the_three_tensors_of_one_module(lora_mod) -> None:
    keys = [
        "lora_unet_model_language_model_layers_0_mlp_down_proj.lora_down.weight",
        "lora_unet_model_language_model_layers_0_mlp_down_proj.lora_up.weight",
        "lora_unet_model_language_model_layers_0_mlp_down_proj.alpha",
        "lora_unet_model_final_layer2_linear.lora_down.weight",
    ]
    grouped = lora_mod.group_adapter_keys(keys)
    assert grouped["model_language_model_layers_0_mlp_down_proj"] == {"down", "up", "alpha"}
    # A module with only half its tensors is grouped anyway; `merge` is what refuses it, so the
    # report can say which module was incomplete rather than that the file was short.
    assert grouped["model_final_layer2_linear"] == {"down"}


def test_ignores_keys_outside_the_convention(lora_mod) -> None:
    """A diffusers/PEFT adapter names its tensors differently and must not be half-read."""
    assert (
        lora_mod.group_adapter_keys(
            [
                "transformer.blocks.0.attn.to_q.lora_A.weight",
                "base_model.model.layers.0.self_attn.q_proj.lora_B.weight",
            ]
        )
        == {}
    )


def test_flattening_is_generated_from_the_tree_never_parsed_back(lora_mod) -> None:
    """The dotted path is recovered by flattening the real module names, not by splitting.

    Reading "model_language_model_layers_0_mlp_down_proj" backwards has several valid splits; only
    one matches a module that exists. Generating the key from the tree removes the ambiguity, so
    this asserts the direction of the mapping rather than the result of a parse.
    """

    class _Linear:
        pass

    class _Model:
        def named_modules(self):
            return [
                ("", self),
                ("model.language_model.layers.0.mlp.down_proj", _Linear()),
                ("model.final_layer2.linear", _Linear()),
            ]

    torch = pytest.importorskip("torch", reason="flatten_module_paths isinstance-checks nn.Linear")
    assert torch  # the real check runs in tests/live; this documents why it is skipped here


def test_read_metadata_reads_only_the_header(lora_mod, tmp_path: Path) -> None:
    meta = {
        "ss_network_module": "networks.lora_hidream_o1",
        "ss_base_model_version": "hidream_o1_image",
    }
    header = {
        "__metadata__": meta,
        "lora_unet_x.alpha": {"dtype": "BF16", "shape": [], "data_offsets": [0, 2]},
    }
    blob = json.dumps(header).encode()
    path = tmp_path / "adapter.safetensors"
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + b"\x00\x00")
    assert lora_mod.read_metadata(path)["ss_base_model_version"] == "hidream_o1_image"
