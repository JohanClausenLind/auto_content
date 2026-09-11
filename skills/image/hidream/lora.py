"""Merge a musubi-tuner HiDream-O1 LoRA into the loaded model, once, at startup.

The romsketch adapter sat on this host for five days with nowhere to plug in: 82 image+caption
pairs of two-person affection, trained twice, and the HiDream skill had no way to load an adapter
at all. This is that way.

**Merged, not attached.** The weights are folded into the base tensors when the server starts, so
generation costs nothing extra and every request sees the same model. The price is that it cannot
be unloaded — a different adapter means restarting the server, which is what ``ensure()`` already
does when it switches GPU tenants.

**Naming.** musubi-tuner writes ``lora_unet_<dotted module path with dots replaced by underscores>``
(``model.language_model.layers.0.mlp.down_proj`` becomes
``lora_unet_model_language_model_layers_0_mlp_down_proj``). Underscores are ambiguous read
backwards — ``down_proj`` and ``language_model`` both contain one — so this never parses the
flattened name. It walks the real module tree, flattens each Linear's own path the same way, and
matches. A key that matches nothing is reported rather than skipped silently: a LoRA that lands on
none of the model is indistinguishable from no LoRA at all, and that is exactly the failure worth
being loud about.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

LORA_PREFIX = "lora_unet_"
SUFFIX_DOWN = ".lora_down.weight"
SUFFIX_UP = ".lora_up.weight"
SUFFIX_ALPHA = ".alpha"


def read_metadata(path: Path) -> dict[str, str]:
    """The safetensors ``__metadata__`` header, without loading a single tensor.

    Cheap enough to call before deciding whether to load at all, which is what lets the server
    refuse a mismatched adapter instead of merging it and producing quiet nonsense.
    """
    with Path(path).open("rb") as fh:
        length = struct.unpack("<Q", fh.read(8))[0]
        header = json.loads(fh.read(length))
    meta = header.get("__metadata__") or {}
    return {str(k): str(v) for k, v in meta.items()}


def group_adapter_keys(keys) -> dict[str, set[str]]:
    """``flattened module name -> which of {down, up, alpha} the checkpoint carries for it``.

    Pure, and separated from :func:`merge` for exactly that reason: this is where an adapter in the
    wrong naming convention is detectable, and it is the half of the loader that can be tested in
    the core suite, which has no torch.
    """
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
    """``flattened name -> dotted module path`` for every Linear in the model.

    Built from the tree rather than from the checkpoint, so the mapping is whatever the model
    actually is. A collision would mean two different modules flatten to one name; it has never
    happened on this architecture, and it raises rather than picking one.
    """
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
    """Fold the adapter at ``path`` into ``model`` in place. Returns what it did.

    ``W += multiplier * (alpha / rank) * (up @ down)``, which is musubi's own convention and the
    same scaling its ``hidream_o1_generate_image.py`` applies at ``--lora_multiplier``.
    """
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
