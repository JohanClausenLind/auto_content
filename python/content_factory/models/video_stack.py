"""The local AI-video model stack: tiers, fallbacks, and an honest on-disk verifier.

Mirrors the ``scripts/download_ai_video_stack.sh`` layout, which since 2026-09-05 is the
repository itself (ComfyUI-style flat dirs): upstream code checkouts in ``<root>/external/``,
weights in a separate store such as ``/mnt/fast/models`` (pass ``models_root``; ``<root>/models/``
is only the category-sorted symlink index into that store), generated media in ``<root>/output/``.
``root`` is ``$AI_VIDEO_ROOT`` or the repo root (see :func:`default_stack_root`).

Reports what is actually usable RIGHT NOW on this machine — per entry: ready / downloading / gated /
repo-without-weights / not downloaded / needs a manual step / known-incompatible. Facts verified
against the Hugging Face + GitHub APIs on 2026-09-05 (revisions pinned in
scripts/download_video_stack_extras.sh):

* LTX-2.3 IC-LoRAs do NOT apply to the LTX-2.5 22B *distilled* GGUF transformer this stack
  downloads, and that is upstream's own statement rather than a reading of a ``base_model``
  field: ``external/LTX-2`` (1.3.0, 2026-08-25) says "a LoRA only works with the model it was
  trained on" and "pair them with an LTX-2.3 checkpoint". The only LTX-2.5 IC-LoRA Lightricks
  publishes is Pixel-Spatial-Upscaler (detailing) — no depth, canny, pose, union or motion-track
  adapter for 2.5 exists to download. Re-checked 2026-09-09.
* Wan-Animate-2 official weights are BF16 14B only (no fp8/quant in the repo) — not a
  comfortable native fit for 24 GB; community GGUF quants exist (pinned, flagged as community).
* Practical-RIFE publishes weights via Google Drive; the HF mirrors are untrusted junk — the
  entry stays a documented manual step rather than pretending a scriptable source exists.
* LivePortrait's official weights moved to KlingTeam/LivePortrait.
* facebook/sam3.1 is manually gated: request access, then rerun the downloader.
"""

from __future__ import annotations

import glob
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class StackTier(StrEnum):
    generation = "generation"
    images = "images"
    video_editing = "video_editing"
    characters = "characters"
    restoration = "restoration"
    interpolation = "interpolation"
    sound = "sound"
    music = "music"
    speech_timing = "speech_timing"
    speech_restoration = "speech_restoration"
    scene_control = "scene_control"
    final_processing = "final_processing"


class StackStatus(StrEnum):
    ready = "ready"
    downloading = "downloading"
    gated_pending = "gated_pending"  # repo reachable, weights behind a license gate
    missing_weights = "missing_weights"  # code repo present, checkpoints absent
    not_downloaded = "not_downloaded"
    manual_step = "manual_step"  # no trustworthy scriptable source; documented manual fetch
    incompatible = "incompatible"  # verified mismatch with what this stack downloads
    wont_fit_24gb = "wont_fit_24gb"  # per the model's own docs/format on this GPU class
    tool_missing = "tool_missing"


@dataclass(frozen=True)
class StackEntry:
    key: str
    tier: StackTier
    name: str
    role: str  # primary | quality | speed | fallback | alternative
    weight_globs: tuple[tuple[str, int], ...] = ()  # (glob under the weight store, min bytes)
    abs_globs: tuple[tuple[str, int], ...] = ()  # absolute-path globs outside the stack root
    repo_globs: tuple[tuple[str, int], ...] = ()  # (glob under external/, min bytes) — weights
    # that upstream tooling insists on finding inside its code checkout (e.g. RIFE's train_log/)
    repo_dir: str | None = None  # expected clone under external/
    tool: str | None = None  # executable on PATH (final-processing tier)
    source: str = ""
    gated: bool = False
    community_quant: bool = False
    static_status: StackStatus | None = None  # verified verdicts that presence cannot change
    caveat: str = ""
    recommendation: str = ""


@dataclass(frozen=True)
class EntryReport:
    entry: StackEntry
    status: StackStatus
    detail: str


@dataclass(frozen=True)
class VideoStackReport:
    root: str
    entries: tuple[EntryReport, ...] = field(default_factory=tuple)
    models_root: str = ""

    def by_status(self, status: StackStatus) -> tuple[EntryReport, ...]:
        return tuple(e for e in self.entries if e.status == status)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.status.value] = out.get(e.status.value, 0) + 1
        return out


VIDEO_STACK: tuple[StackEntry, ...] = (
    # ---- GENERATION -------------------------------------------------------------------------
    StackEntry(
        key="ltx-2.5",
        tier=StackTier.generation,
        name="LTX-2.5 22B distilled (Q5_K_M GGUF)",
        role="primary",
        weight_globs=(
            (
                "ltx25/diffusion_models/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
                15_000_000_000,
            ),
            ("ltx25/text_encoders/gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf", 8_000_000_000),
            ("ltx25/vae/ltx-2.5-video-vae-conv-bf16.safetensors", 1_000_000_000),
            ("ltx25/vae/ltx-2.5-audio-vae-bf16.safetensors", 200_000_000),
            ("ltx25/latent_upscale_models/*.safetensors", 500_000_000),
        ),
        repo_dir="LTX-2",
        source="elix3r/LTX-2.5-22b-distilled-GGUF + Lightricks/LTX-2.5 (VAEs, upscaler)",
        caveat=(
            "quantized 3090 profile; audio VAE included so clips can carry sound. Known limit"
            " (verified 2026-09-05): the gemma GGUF ships no tokenizer.ggml metadata, so pure"
            " text-to-video fails — drive it image-to-video (HiDream keyframe first)"
        ),
    ),
    StackEntry(
        key="wan-2.2-t2v",
        tier=StackTier.generation,
        name="Wan 2.2 T2V A14B (Q5_K_M GGUF pair)",
        role="alternative",
        weight_globs=(
            ("wan2.2/**/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf", 8_000_000_000),
            ("wan2.2/**/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf", 8_000_000_000),
            ("wan2.2/**/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 5_000_000_000),
            ("wan2.2/**/wan2.2_vae.safetensors", 500_000_000),
        ),
        repo_dir="Wan2.2",
        source="QuantStack/Wan2.2-T2V-A14B-GGUF + Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        community_quant=True,
        caveat="not in the base downloader; scripts/download_video_stack_extras.sh fetches it",
    ),
    # ---- IMAGES (keyframes, style reference, image editing) ---------------------------------
    StackEntry(
        key="hidream-o1",
        tier=StackTier.images,
        name="HiDream-O1-Image (8B dev checkpoint)",
        role="primary",
        weight_globs=(("hidream-o1/model-*.safetensors", 1_000_000_000),),
        source="HiDream-ai/HiDream-O1-Image (already integrated: skills/image/hidream)",
        caveat=(
            "served by this repo's own local HiDream server skill; canonical weights live in the"
            " weight store (/mnt/fast/models/hidream-o1 on this host), reached by the skill via"
            " the models/image_generation/HiDream-O1-Image-Dev index link"
        ),
    ),
    StackEntry(
        key="flux2-dev",
        tier=StackTier.images,
        name="FLUX.2-dev (Q4_K_M GGUF) + Mistral-Small-3.2 encoder",
        role="primary",
        weight_globs=(
            ("flux2-dev/diffusion_models/flux2-dev-Q4_K_M.gguf", 15_000_000_000),
            ("flux2-dev/text_encoders/mistral_3_small_flux2_fp8.safetensors", 12_000_000_000),
            ("flux2-dev/vae/flux2-vae.safetensors", 200_000_000),
            ("flux2-dev/loras/Flux_2-Turbo-LoRA_comfyui.safetensors", 1_000_000_000),
        ),
        source="city96/FLUX.2-dev-gguf + Comfy-Org/flux2-dev + ByteZSzn/Flux.2-Turbo-ComfyUI",
        community_quant=True,
        caveat=(
            "the multi-reference compositor, not a second text-to-image model: it is what holds a"
            " character across frames, which HiDream measurably does not — over thirty staged"
            " anchors HiDream kept one world and changed the outfit four times. Driven through"
            " ComfyUI (media/flux2_packages.py) rather than a bespoke server, so the weights must"
            " be visible in the comfy workspace's models/ tree. Q4_K_M at 18.7 GB plus a 16.8 GB"
            " fp8 text encoder is 35.5 GB against a 24 GB card, so the two load in sequence and"
            " the encoder is pinned to the CPU; the Turbo LoRA is what makes the step count sane"
        ),
        recommendation="8 steps at guidance 1.0 with the Turbo LoRA; 28 at 4.0 without",
    ),
    # ---- VIDEO EDITING ----------------------------------------------------------------------
    StackEntry(
        key="cutie",
        tier=StackTier.video_editing,
        name="Cutie (object/person tracking + segmentation)",
        role="primary",
        weight_globs=(("cutie/cutie-base-mega.pth", 50_000_000),),
        repo_dir="Cutie",
        source="github.com/hkchengrex/Cutie (weight via sczhou/ProPainter release v0.1.0)",
        caveat="ungated; the working alternative while the SAM 3.1 gate is pending",
    ),
    StackEntry(
        key="sam-3.1",
        tier=StackTier.video_editing,
        name="SAM 3.1 (segmentation/tracking)",
        role="quality",
        weight_globs=(("sam3.1/**/*.pt", 100_000_000),),
        repo_dir="sam3",
        source="facebook/sam3.1",
        gated=True,
        caveat="MANUALLY gated: request access on Hugging Face, then rerun the downloader",
        recommendation="use Cutie until the Meta access request is approved",
    ),
    StackEntry(
        key="ltx-2.3-ic-loras",
        tier=StackTier.video_editing,
        name="LTX-2.3 editing IC-LoRAs (In-Outpainting, Clean-Plate, Relight)",
        role="quality",
        weight_globs=(("ltx23-ic-loras/**/*.safetensors", 10_000_000),),
        source="Lightricks/LTX-2.3-22b-IC-LoRA-*",
        static_status=StackStatus.incompatible,
        caveat=(
            "not our inference from a base_model field: Lightricks states it. external/LTX-2"
            " (1.3.0, 2026-08-25) README 'Legacy: LTX-2.3' — 'a LoRA only works with the model it"
            " was trained on'; MODELS-LTX-2.3.md — 'The LoRAs at the end of this page were trained"
            " on LTX-2.3, so pair them with an LTX-2.3 checkpoint.' Union-Control is one of them."
            " Re-checked 2026-09-09"
        ),
        recommendation=(
            "either also download the LTX-2.3 22B base (own VRAM budget) or do editing with"
            " SAM 3.1 + ProPainter; the only 2.5 IC-LoRA upstream publishes is"
            " Pixel-Spatial-Upscaler (detailing) — there is no 2.5 depth/canny/pose/union adapter"
        ),
    ),
    StackEntry(
        key="joyai-video-edit",
        tier=StackTier.video_editing,
        name="JoyAI-Video-Edit",
        role="quality",
        weight_globs=(("joyai-video-edit/dit/*.pth", 1_000_000_000),),
        repo_dir="JoyAI-Video-Edit",
        source="jdopensource/JoyAI-Video-Edit (+ MiMo-VL-7B-RL-2508)",
        static_status=StackStatus.wont_fit_24gb,
        caveat="~51 GB of dependencies; its documented low-VRAM path targets 32 GB-class GPUs",
        recommendation="skip on the 3090; use SAM 3.1 + ProPainter, or rent a larger GPU for it",
    ),
    StackEntry(
        key="propainter",
        tier=StackTier.video_editing,
        name="ProPainter (video inpainting)",
        role="fallback",
        weight_globs=(
            ("propainter/ProPainter.pth", 100_000_000),
            ("propainter/raft-things.pth", 10_000_000),
            ("propainter/recurrent_flow_completion.pth", 10_000_000),
        ),
        repo_dir="ProPainter",
        source="github.com/sczhou/ProPainter releases v0.1.0",
        caveat=(
            "NOT in the base downloader (extras script adds it). S-Lab License 1.0:"
            " NON-COMMERCIAL — check against how the output is used"
        ),
    ),
    # ---- CHARACTERS -------------------------------------------------------------------------
    StackEntry(
        key="wan-animate-2",
        tier=StackTier.characters,
        name="Wan-Animate-2 14B (Q5_K_M community GGUF)",
        role="quality",
        weight_globs=(("wan-animate-2/wan_animate_2-Q5_K_M.gguf", 8_000_000_000),),
        source="karcsiha/wan_animate_2_gguf (community quant of Wan-AI/Wan2.2-Animate-2-14B)",
        community_quant=True,
        caveat=(
            "official repo is BF16-only (~82 GB, no fp8/quant) — not a native 24 GB fit;"
            " this community quant is pinned by revision and flagged as such"
        ),
    ),
    StackEntry(
        key="liveportrait",
        tier=StackTier.characters,
        name="LivePortrait (fast face/portrait)",
        role="speed",
        weight_globs=(
            ("liveportrait/liveportrait/landmark.onnx", 10_000_000),
            ("liveportrait/liveportrait/base_models/*.pth", 1_000_000),
        ),
        source="KlingTeam/LivePortrait (official weights moved from KwaiVGI)",
        caveat=(
            "NOT in the base downloader (extras script adds it); the insightface dependency is"
            " non-commercial research — same rights check as ProPainter"
        ),
    ),
    # ---- RESTORATION ------------------------------------------------------------------------
    StackEntry(
        key="seedvr2",
        tier=StackTier.restoration,
        name="SeedVR2 3B fp16",
        role="primary",
        weight_globs=(
            ("seedvr2/diffusion_models/seedvr2_3b_fp16.safetensors", 4_000_000_000),
            ("seedvr2/vae/seedvr2_ema_vae_fp16.safetensors", 100_000_000),
        ),
        source="Comfy-Org/SeedVR2 (3B fp16 subset, not the 113 GB repo)",
        caveat="run only on approved shots/final renders, never on rejected drafts",
    ),
    # ---- FRAME INTERPOLATION ----------------------------------------------------------------
    StackEntry(
        key="gimm-vfi",
        tier=StackTier.interpolation,
        name="GIMM-VFI",
        role="quality",
        weight_globs=(
            ("gimm-vfi/gimmvfi_f_arb.pt", 10_000_000),
            ("gimm-vfi/gimmvfi_r_arb.pt", 10_000_000),
            ("gimm-vfi/flowformer_sintel.pth", 10_000_000),
            ("gimm-vfi/raft-things.pth", 10_000_000),
        ),
        repo_dir="GIMM-VFI",
        source="GSean/GIMM-VFI (author's checkpoints)",
        caveat="base downloader clones the code only; extras script fetches the checkpoints",
    ),
    StackEntry(
        key="vfimamba",
        tier=StackTier.interpolation,
        name="VFIMamba",
        role="quality",
        weight_globs=(("vfimamba/model.pkl", 1_000_000),),
        repo_dir="VFIMamba",
        source="MCG-NJU/VFIMamba (official checkpoints)",
        caveat="base downloader clones the code only; extras script fetches the checkpoints",
    ),
    StackEntry(
        key="rife",
        tier=StackTier.interpolation,
        name="Practical-RIFE (4.25)",
        role="speed",
        repo_globs=(("Practical-RIFE/train_log/flownet.pkl", 1_000_000),),
        repo_dir="Practical-RIFE",
        source="github.com/hzwer/Practical-RIFE (weights are a manual Google Drive fetch)",
        static_status=StackStatus.manual_step,
        caveat=(
            "official weights ship via Google Drive links in the repo README (HF mirrors are"
            " untrusted junk) — place train_log/ inside the Practical-RIFE checkout; done"
            " 2026-09-05 (4.25) during the model consolidation"
        ),
    ),
    # ---- SOUND ------------------------------------------------------------------------------
    StackEntry(
        key="stable-audio-3-sfx",
        tier=StackTier.sound,
        name="Stable Audio 3 Small SFX (text→SFX)",
        role="primary",
        weight_globs=(("stable-audio-3-small-sfx/**/*.safetensors", 100_000_000),),
        repo_dir="stable-audio-3",
        source="stabilityai/stable-audio-3-small-sfx",
        gated=True,
        caveat="auto-gated (click-through terms)",
    ),
    StackEntry(
        key="prismaudio",
        tier=StackTier.sound,
        name="PrismAudio (video→Foley/SFX)",
        role="primary",
        weight_globs=(("prismaudio/**/*.safetensors", 100_000_000),),
        repo_dir="PrismAudio",
        source="QwenAudio/ThinkSound @ prismaudio branch",
        caveat=(
            "code cloned; weights are fetched by the repo on first run — verify its manifest"
            " before an offline/pinned deployment"
        ),
    ),
    StackEntry(
        key="mmaudio",
        tier=StackTier.sound,
        name="MMAudio large 44k v2",
        role="fallback",
        weight_globs=(("mmaudio/**/mmaudio_large_44k_v2.pth", 500_000_000),),
        repo_dir="MMAudio",
        source="hkchengrex/MMAudio",
        caveat="auto-fetches its shared dependency nets on first run",
    ),
    # ---- MUSIC ------------------------------------------------------------------------------
    StackEntry(
        key="ace-step-1.5",
        tier=StackTier.music,
        name="ACE-Step 1.5 (Turbo)",
        role="primary",
        weight_globs=(("ace-step-1.5/**/*.safetensors", 500_000_000),),
        repo_dir="ACE-Step-1.5",
        source="ACE-Step/Ace-Step1.5",
        caveat=(
            "verified 2026-09-05: the official pack ships acestep-v15-turbo + LM + VAE only —"
            " no XL/SFT artifact is published; instrumental stems unless lyrics are required"
        ),
    ),
    # ---- SPEECH / TIMING --------------------------------------------------------------------
    StackEntry(
        key="qwen3-tts-customvoice",
        tier=StackTier.speech_timing,
        name="Qwen3-TTS 12Hz 1.7B CustomVoice (narration voice)",
        role="primary",
        weight_globs=(
            ("qwen3-tts-1.7b-customvoice/model.safetensors", 3_000_000_000),
            ("qwen3-tts-1.7b-customvoice/speech_tokenizer/config.json", 10),
        ),
        source="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice (Apache-2.0 code and weights)",
        caveat=(
            "nine built-in timbres, ten languages, natural-language delivery control, ~5 GB at"
            " bf16 — but it returns NO word timings, so every beat is force-aligned afterwards"
            " (verified 2026-09-07: faster-whisper base.en matched an 11-word beat 1:1 at"
            " similarity 0.91). CPU generation is impractically slow; this one wants the card."
            " The fallback, Kokoro-82M, is deliberately absent from this index: its weights come"
            " down with the `kokoro` wheel into skills/audio/kokoro, so there is nothing here to"
            " verify — CF__NARRATION__TTS=kokoro selects it and it times its own tokens"
        ),
        recommendation="CF__NARRATION__TTS=qwen3tts with CF__NARRATION__ALIGNER=faster_whisper",
    ),
    StackEntry(
        key="qwen3-tts-base",
        tier=StackTier.speech_timing,
        name="Qwen3-TTS 12Hz 1.7B Base (voice cloning)",
        role="alternative",
        weight_globs=(("qwen3-tts-1.7b-base/model.safetensors", 3_000_000_000),),
        source="Qwen/Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0)",
        caveat=(
            "declares no built-in speakers at all — it is the clone base: give it ~3 s of"
            " reference audio AND that clip's transcript (CF__NARRATION__QWEN_REF_AUDIO/_REF_TEXT)"
        ),
    ),
    StackEntry(
        key="whisperx",
        tier=StackTier.speech_timing,
        name="WhisperX (word-level alignment)",
        role="primary",
        repo_dir="whisperX",
        source="github.com/m-bain/whisperX",
        caveat="ASR/alignment models download on first use; aligns the approved script,"
        " never rewrites it",
    ),
    # ---- SPEECH RESTORATION (the voice chain between the take and the mix) ------------------
    StackEntry(
        key="resemble-enhance",
        tier=StackTier.speech_restoration,
        name="Resemble Enhance (denoiser + latent CFM + UnivNet, 44.1 kHz)",
        role="primary",
        weight_globs=(
            (
                "resemble-enhance/enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt",
                600_000_000,
            ),
            ("resemble-enhance/enhancer_stage2/hparams.yaml", 100),
        ),
        repo_dir="resemble-enhance",
        source="ResembleAI/resemble-enhance (enhancer_stage2 only; MIT code AND weights)",
        caveat=(
            "verified 2026-09-07: rebuilds the band a 24 kHz TTS never produced (nothing above"
            " 12 kHz -> 0.17 % of energy above it) and preserves length to the sample; RTF 18.6 on"
            " CPU, so a real run wants --device cuda between the GPU stages. Its environment needs"
            " deepspeed (an import of the training Engine) and numpy<2 (the CFM solver)"
        ),
        recommendation=(
            "the main restoration step: CF__SPEECH_RESTORATION__ENHANCER=resemble_enhance"
        ),
    ),
    StackEntry(
        key="mossformer2-se-48k",
        tier=StackTier.speech_restoration,
        name="ClearerVoice MossFormer2_SE_48K (speech enhancement)",
        role="alternative",
        weight_globs=(("mossformer2-se-48k/last_best_checkpoint.pt", 200_000_000),),
        source="alibabasglab/MossFormer2_SE_48K (Apache-2.0)",
        caveat=(
            "cleanup only: verified 2026-09-07 to leave the band limit where it found it"
            " (12.0 -> 11.6 kHz), so it is gated on the noise measurements, not run by default"
        ),
    ),
    StackEntry(
        key="mossformer2-sr-48k",
        tier=StackTier.speech_restoration,
        name="ClearerVoice MossFormer2_SR_48K (48 kHz speech super-resolution)",
        role="quality",
        weight_globs=(
            ("mossformer2-sr-48k/last_best_checkpoint_m.pt", 200_000_000),
            ("mossformer2-sr-48k/last_best_checkpoint_g.pt", 200_000_000),
        ),
        source=(
            "alibabasglab/MossFormer2_SR_48K (Apache-2.0; its 1.7 GB do_03925000 is"
            " training state, not downloaded)"
        ),
        caveat=(
            "band extension: verified 2026-09-07 to lift a 24 kHz take's band limit 12.0 ->"
            " 19.3 kHz at RTF 3.0 on CPU. Upstream's writer resamples back to the input rate,"
            " which would undo it — the skill writes 48 kHz itself"
        ),
    ),
    # ---- SCENE CONTROL (3D layer: Blender control passes) -----------------------------------
    StackEntry(
        key="blender",
        tier=StackTier.scene_control,
        name="Blender 5.2 LTS (headless control-pass renderer)",
        role="primary",
        tool="blender",
        source="snap install blender --classic",
        caveat="control passes only: Cycles CPU 1 spp for depth/normals/object index, Workbench for"
        " the rough RGB (needs a GPU context in --background; falls back to Cycles CPU). MPFB2 is"
        " needed only to build character assets, never at render time",
        recommendation="skills/video/blender_scene/render.py <shot_spec.json> <out_dir>"
        " (CF__CONTROLS__COMPILER=blender)",
    ),
    StackEntry(
        key="blender-characters",
        tier=StackTier.scene_control,
        name="MPFB2 character assets (rigged, baked OpenPose anchors)",
        role="primary",
        abs_globs=(("/mnt/fast/models/blender-assets/characters/*/*.blend", 1_000_000),),
        source="built locally by skills/video/blender_scene/assets_build (MakeHuman CC0 meshes,"
        " MPFB2 GPL tooling)",
        caveat="GPL-derived vertex tables live under the weight store as baked .blend assets;"
        " only recipes and pose manifests are in the repo",
        recommendation="skills/video/blender_scene/assets_build: install MPFB into Blender 5.2,"
        " then build_character.py per recipe",
    ),
    # ---- FINAL PROCESSING -------------------------------------------------------------------
    StackEntry(
        key="ffmpeg",
        tier=StackTier.final_processing,
        name="FFmpeg",
        role="primary",
        tool="ffmpeg",
        source="system package",
        caveat="node-report verified h264_nvenc/hevc_nvenc on this 3090; av1_nvenc FAILED"
        " (no AV1 encode on Ampere) — deliver AV1 via libsvtav1 (CPU) if ever needed",
    ),
    StackEntry(
        key="pyscenedetect",
        tier=StackTier.final_processing,
        name="PySceneDetect",
        role="primary",
        tool="scenedetect",
        source="PyPI",
        caveat="scene-cut awareness for interpolation chunking and editing",
    ),
)


def _has_incomplete(models_root: Path, first_glob: str) -> bool:
    top = first_glob.split("/", 1)[0]
    subdir = models_root / top
    if not subdir.exists():
        return False
    return any(subdir.rglob("*.incomplete"))


def verify_video_stack(
    root: Path,
    *,
    models_root: Path | None = None,
    entries: tuple[StackEntry, ...] = VIDEO_STACK,
    which: Callable[[str], str | None] = shutil.which,
) -> VideoStackReport:
    """Pure filesystem/PATH inspection — no network, no model loads, safe while downloads run.

    ``models_root`` overrides ``root/models`` when the weight store lives elsewhere (the
    operator moved it to ``/mnt/fast/models`` on 2026-09-05; ``<repo>/models/<category>/<Name>``
    is only a human-readable symlink index and is not scanned)."""
    models = models_root if models_root is not None else root / "models"
    repos = root / EXTERNAL_DIR
    reports: list[EntryReport] = []
    for e in entries:
        if e.tool is not None:
            found = which(e.tool)
            reports.append(
                EntryReport(
                    e,
                    StackStatus.ready if found else StackStatus.tool_missing,
                    found or f"{e.tool} not on PATH",
                )
            )
            continue

        missing: list[str] = []
        for pattern, min_bytes in e.weight_globs:
            matches = [p for p in models.glob(pattern) if p.is_file() and not p.is_symlink()]
            if not any(p.stat().st_size >= min_bytes for p in matches):
                missing.append(pattern)
        for pattern, min_bytes in e.abs_globs:
            abs_matches = [
                Path(m) for m in glob.glob(str(Path(pattern).expanduser()), recursive=True)
            ]
            if not any(m.is_file() and m.stat().st_size >= min_bytes for m in abs_matches):
                missing.append(pattern)
        for pattern, min_bytes in e.repo_globs:
            repo_matches = [p for p in repos.glob(pattern) if p.is_file()]
            if not any(p.stat().st_size >= min_bytes for p in repo_matches):
                missing.append(pattern)
        repo_present = e.repo_dir is not None and (repos / e.repo_dir).is_dir()
        weights_present = bool(e.weight_globs or e.abs_globs or e.repo_globs) and not missing

        # incompatible/wont_fit are verified verdicts presence cannot change; manual_step
        # clears the moment the operator has actually done the manual step.
        sticky = e.static_status in {StackStatus.incompatible, StackStatus.wont_fit_24gb}
        if e.static_status is not None and (sticky or not weights_present):
            reports.append(EntryReport(e, e.static_status, e.caveat))
            continue
        if weights_present:
            status, detail = StackStatus.ready, "all expected weights present"
        elif e.weight_globs and _has_incomplete(models, e.weight_globs[0][0]):
            status, detail = StackStatus.downloading, "partial .incomplete files present"
        elif e.gated and not weights_present:
            status, detail = StackStatus.gated_pending, e.caveat or "license gate not cleared"
        elif repo_present and e.weight_globs:
            status, detail = StackStatus.missing_weights, f"repo cloned; missing: {missing}"
        elif repo_present and not e.weight_globs:
            status, detail = StackStatus.ready, "code repo present (models fetch on first use)"
        else:
            status, detail = StackStatus.not_downloaded, f"missing: {missing or [e.repo_dir]}"
        reports.append(EntryReport(e, status, detail))
    return VideoStackReport(root=str(root), entries=tuple(reports), models_root=str(models))


REPO_ROOT = Path(__file__).resolve().parents[3]
EXTERNAL_DIR = "external"  # upstream model-code checkouts (+ their .venv), git-ignored


def default_stack_root() -> Path:
    """``$AI_VIDEO_ROOT`` when set, else the repository root.

    Since 2026-09-05 the local stack is laid out ComfyUI-style inside the repo, all git-ignored:
    ``external/`` (upstream checkouts, formerly ``~/ai-video-stack/repos``), ``models/`` (the
    category-sorted symlink index into the weight store, formerly ``~/models``), ``output/``
    (generated media and eval runs) and ``.venvs/`` (the hf download and scenedetect tool venvs)."""
    configured = os.environ.get("AI_VIDEO_ROOT", "")
    if configured:
        return Path(configured).expanduser()
    return REPO_ROOT


def resolve_models_root(
    root: Path,
    *,
    explicit: str = "",
    configured_roots: tuple[str, ...] = (),
) -> Path:
    """Where the weights are: an explicit path (flag/env) wins; else the first existing
    configured inventory root (``CF__COMFYUI__EXTRA_MODEL_ROOTS`` points at the same store the
    Models panel scans); else ``root/models``. The configured store must beat ``root/models``
    because on this layout ``<repo>/models/`` exists but holds only the symlink index."""
    if explicit:
        return Path(explicit).expanduser()
    for candidate in configured_roots:
        path = Path(candidate).expanduser()
        if path.is_dir():
            return path
    return index_store_root(root / "models") or root / "models"


def index_store_root(index: Path) -> Path | None:
    """The weight store a ``<repo>/models/<category>/<Name>`` symlink index points into, or None
    when ``index`` is not such an index. Every link in the index resolves to ``<store>/<short>``,
    so the store is the common parent of the resolved targets; the index itself holds no weights,
    and scanning it made the report claim NO WEIGHTS for a complete stack (2026-09-06)."""
    if not index.is_dir():
        return None
    parents: set[Path] = set()
    for category in index.iterdir():
        if not category.is_dir() or category.is_symlink():
            continue
        for entry in category.iterdir():
            if entry.is_symlink():
                try:
                    parents.add(entry.resolve(strict=True).parent)
                except OSError:
                    continue
    if len(parents) != 1:
        return None
    (store,) = parents
    return store if store != index and store.is_dir() else None
