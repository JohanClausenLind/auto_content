"""Runs INSIDE the Cutie checkout's environment: propagate an indexed mask through a frame dir.

    python cutie_driver.py <frames_dir> <seed_mask.png> <masks_out_dir> [--weights PATH]
        [--max-internal-size 480] [--mem-every 5]

The seed mask is an indexed or grayscale PNG with 0 = background (the Blender segmentation pass is
exactly that). Output: one indexed PNG per frame with the same ids.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir")
    ap.add_argument("seed_mask")
    ap.add_argument("out_dir")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--max-internal-size", type=int, default=480)
    ap.add_argument("--mem-every", type=int, default=5)
    args = ap.parse_args()

    import numpy as np
    import torch
    from cutie.inference.inference_core import InferenceCore
    from cutie.utils.get_default_model import get_default_model
    from omegaconf import open_dict
    from PIL import Image
    from torchvision.transforms.functional import to_tensor

    frames = sorted(
        p for p in Path(args.frames_dir).iterdir() if p.suffix.lower() in (".png", ".jpg")
    )
    seed = Image.open(args.seed_mask)
    if seed.mode not in ("L", "P"):
        seed = seed.convert("L")
    palette = seed.getpalette()
    objects = [int(v) for v in np.unique(np.array(seed)) if v != 0]
    if not objects:
        print("seed mask has no objects", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    weights = args.weights or os.environ.get("CF_CUTIE_WEIGHTS")
    with torch.inference_mode():
        cutie = (
            get_default_model() if not weights else _model_with_weights(weights, get_default_model)
        )
        cfg = cutie.cfg
        with open_dict(cfg):
            cfg["mem_every"] = args.mem_every
        processor = InferenceCore(cutie, cfg=cfg)
        processor.max_internal_size = args.max_internal_size
        mask_t = torch.from_numpy(np.array(seed)).cuda()
        for i, frame in enumerate(frames):
            image = to_tensor(Image.open(frame).convert("RGB")).cuda().float()
            with torch.autocast("cuda", dtype=torch.float16):
                prob = (
                    processor.step(image, mask_t, objects=objects)
                    if i == 0
                    else processor.step(image)
                )
            mask = processor.output_prob_to_mask(prob).cpu().numpy().astype(np.uint8)
            out = Image.fromarray(mask, mode="P")
            if palette:
                out.putpalette(palette)
            out.save(out_dir / f"{i:04d}.png", format="PNG", optimize=False, compress_level=6)
    print(f"cutie: {len(frames)} masks -> {out_dir}")
    return 0


def _model_with_weights(weights: str, get_default_model):
    """Cutie's default loader hard-codes ``output/cutie-base-mega.pth``; point it at ours."""
    import torch
    from cutie.inference.utils.args_utils import get_dataset_cfg
    from cutie.model.cutie import CUTIE
    from hydra import compose, initialize
    from hydra.core.global_hydra import GlobalHydra
    from omegaconf import open_dict

    GlobalHydra.instance().clear()
    initialize(version_base="1.3.2", config_path="../cutie/config", job_name="eval_config")
    cfg = compose(config_name="eval_config")
    with open_dict(cfg):
        cfg["weights"] = weights
    get_dataset_cfg(cfg)
    cutie = CUTIE(cfg).cuda().eval()
    cutie.load_weights(torch.load(cfg.weights, map_location="cpu"))
    return cutie


if __name__ == "__main__":
    raise SystemExit(main())
