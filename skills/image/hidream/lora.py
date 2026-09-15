"""Merge a musubi-tuner HiDream-O1 LoRA into the loaded model, once, at startup."""

from __future__ import annotations

import json
import struct
from pathlib import Path

LORA_PREFIX = "lora_unet_"
SUFFIX_DOWN = ".lora_down.weight"
SUFFIX_UP = ".lora_up.weight"
SUFFIX_ALPHA = ".alpha"


def read_metadata(path: Path) -> dict[str, str]:
    """The safetensors ``__metadata__`` header, without loading a single tensor."""
    with Path(path).open("rb") as fh:
        length = struct.unpack("<Q", fh.read(8))[0]
        header = json.loads(fh.read(length))
    meta = header.get("__metadata__") or {}
    return {str(k): str(v) for k, v in meta.items()}


def group_adapter_keys(keys) -> dict[str, set[str]]:
    """``flattened module name -> which of {down, up, alpha} the checkpoint carries for it``."""
    out: dict[str, set[str]] = {}
    for key in keys:
        if not key.startswith(LORA_PREFIX):
            continue
        for suffix, slot in ((SUFFIX_DOWN, "down"), (SUFFIX_UP, "up"), (SUFFIX_ALPHA, "alpha")):
            if key.endswith(suffix):
                out.setdefault(key[len(LORA_PREFIX) : -len(suffix)], set()).add(slot)
                break
    return out


def flatten_module_paths(model) -> dict[str, str]:
    """``flattened name -> dotted module path`` for every Linear in the model."""
    import torch

    out: dict[str, str] = {}
    for name, module in model.named_modules():
        if not isinstance(module, torch.nn.Linear):
            continue
        flat = name.replace(".", "_")
        if flat in out:
            msg = f"module names {out[flat]!r} and {name!r} both flatten to {flat!r}"
            raise ValueError(msg)
        out[flat] = name
    return out


def _module_at(model, dotted: str):
    node = model
    for part in dotted.split("."):
        node = getattr(node, part) if not part.isdigit() else node[int(part)]
    return node


def merge(model, path: Path, multiplier: float = 1.0) -> dict[str, object]:
    """Fold the adapter at ``path`` into ``model`` in place."""
    import torch
    from safetensors.torch import load_file

    path = Path(path)
    tensors = load_file(str(path))
    slots = group_adapter_keys(tensors)
    by_module: dict[str, dict[str, torch.Tensor]] = {
        flat: {
            slot: tensors[f"{LORA_PREFIX}{flat}{suffix}"]
            for slot, suffix in (
                ("down", SUFFIX_DOWN),
                ("up", SUFFIX_UP),
                ("alpha", SUFFIX_ALPHA),
            )
            if slot in present
        }
        for flat, present in slots.items()
    }

    lookup = flatten_module_paths(model)
    merged: list[str] = []
    unmatched: list[str] = []
    for flat, parts in sorted(by_module.items()):
        dotted = lookup.get(flat)
        if dotted is None:
            unmatched.append(flat)
            continue
        down, up = parts.get("down"), parts.get("up")
        if down is None or up is None:
            unmatched.append(flat)
            continue
        target = _module_at(model, dotted)
        weight = target.weight
        rank = down.shape[0]
        alpha = float(parts["alpha"].item()) if "alpha" in parts else float(rank)
        scale = multiplier * (alpha / rank)
        delta = (up.to(torch.float32) @ down.to(torch.float32)) * scale
        with torch.no_grad():
            weight.add_(delta.to(device=weight.device, dtype=weight.dtype))
        merged.append(dotted)

    if not merged:
        msg = (
            f"{path.name}: none of its {len(by_module)} modules matched this model. "
            "A LoRA that lands nowhere is not a LoRA — check that the server's weights are the "
            "ones it was trained against (ss_base_model_version in its metadata says which)."
        )
        raise ValueError(msg)
    return {
        "adapter": path.name,
        "multiplier": multiplier,
        "modules_merged": len(merged),
        "modules_unmatched": len(unmatched),
        "unmatched_sample": unmatched[:5],
    }
