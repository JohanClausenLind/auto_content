# Post-processing chain skill

One thin runner for the five tools behind the `fix_video`, `upscale_video` and `interpolate`
stages. Each tool executes inside its **upstream checkout's own environment**
(`external/<repo>/.venv/bin/python`, or `CF_<TOOL>_PYTHON`); this skill's env carries only the
runner. Frames go in and out as PNG directories (`frames/%04d.png`), mp4 only at the ends of the
chain, so any order of tools composes.

| tool | checkout | env on this host | weights | contract |
| --- | --- | --- | --- | --- |
| `cutie` | `external/Cutie` | build with `setup_envs.sh cutie` | `/mnt/fast/models/cutie/cutie-base-mega.pth` | `params.seed_mask` = indexed PNG, 0 = background (the Blender segmentation pass); writes one indexed mask per frame |
| `propainter` | `external/ProPainter` | `setup_envs.sh propainter` | `/mnt/fast/models/propainter/*.pth` (linked to `<repo>/weights`) | `params.mask_dir`; nonzero = inpaint. S-Lab License 1.0, **non-commercial** |
| `seedvr2` | `external/seedvr2_videoupscaler` | present (`.venv`, torch 2.11 cu128) | `/mnt/fast/models/seedvr2-7b` | `--output_format png`, `batch_size` 4n+1 |
| `gimm_vfi` | `external/GIMM-VFI` | `setup_envs.sh gimm_vfi` | `/mnt/fast/models/gimm-vfi/*.pt` (linked to `<repo>/pretrained_ckpt`) | `params.factor` 2/4/8 |
| `rife` | `external/Practical-RIFE` | present (`.venv`, torch 2.11 cu128) | `<repo>/train_log/flownet.pkl` (4.25, manual fetch) | `params.factor` 2/4/8 |

```bash
cd skills/video/postchain && uv sync
uv run --project skills/video/postchain python skills/video/postchain/run.py job.json
# job.json = {"tool": "rife", "frames_dir": "…/frames", "out_dir": "…/interpolated", "params": {"factor": 2}}
```

The last stdout line is one JSON summary (`ok`, `tool`, `frames`, `frames_dir`, `sha256`,
`elapsed_s`); exit codes 0 ok · 2 bad job · 3 tool failed · 4 missing interpreter or weights.
The control plane drives it from `python/content_factory/postchain/` with a fake runner in tests.
