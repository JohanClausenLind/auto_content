"""The installable weight registry: what each declared model requirement *is*, where it comes
from, where it lands, and what must be linked once it is there.

Until now three places knew fragments of this and none knew all of it. ``workflows/*.yaml``
declares requirements (a filename, a folder, a directory substring) so the canvas can show a
readiness column. ``models/video_stack.py`` knows what a family is for and whether it is on disk.
``scripts/download_ai_video_stack.sh`` and ``scripts/download_video_stack_extras.sh`` know the one
thing the UI needed and never had: the repository, the pinned revision, and the destination. That
split is why the Models panel could tell an operator a weight was missing and then offer nothing
but "See the skill's README for the fetch command" — the browser held a requirement it had no way
to satisfy.

This module is the missing half, as data:

* ``store_dir`` — the directory under the weight store (``/mnt/fast/models`` on this host) that
  holds the family, exactly as the download scripts lay it out.
* ``hf`` / ``releases`` — pinned sources. Every Hugging Face revision here is a 40-hex commit,
  either copied from ``download_video_stack_extras.sh`` (verified 2026-09-05) or resolved from
  the Hub API on 2026-09-09; the licence and gating fields were read off the same API call, not
  remembered. ``gated="auto"`` means "accept the terms once while logged in", ``"manual"`` means
  a human has to approve the request, and both are reported to the operator instead of failing
  with a 401 nobody can interpret.
* ``provides`` — the files the family puts in the store, with the ComfyUI folder each one has to
  appear in when ComfyUI is the thing that loads it. Presence is decided by these paths, so a
  half-finished transfer reads as missing rather than installed.
* ``index_category`` / ``index_name`` — the ``<repo>/models/<category>/<Name>`` symlink that makes
  the store readable and is what the skills resolve their weights through. Installing a family
  and not creating this link leaves the weights invisible to the code that needs them, so the
  installer always creates it.

Nothing here downloads anything: this is the declaration, and
:mod:`content_factory.models.weight_install` is the only thing that acts on it. A family with no
trustworthy scriptable source (Practical-RIFE's Google Drive weights) carries ``manual`` prose
instead of a fake URL, because inventing a source is worse than admitting there is none.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel
from content_factory.schemas.workflow_template import ModelRequirement

HF_HOST = "huggingface.co"
# Release assets come from the project's own GitHub releases (ProPainter, Cutie). Kept separate
# from the paste-a-URL allowlist in models/install.py: those two hosts are what an operator may
# type, these are pinned URLs this file declares.
RELEASE_HOSTS = ("github.com",)

Gating = Literal["no", "auto", "manual"]


class ProvidedFile(SchemaModel):
    """One file the family puts in the store, and where ComfyUI needs to see it."""

    store_rel: str = Field(min_length=1, max_length=300)
    """Path under ``<store>/<store_dir>``, as the download lays it down."""
    comfy_folder: str = Field(default="", pattern=r"^(models/[a-z_]+)?$")
    """``models/<folder>`` when ComfyUI is what loads this file; empty when a skill loads it."""
    min_bytes: int = Field(default=1, ge=1)
    """Below this the file is a leftover from an interrupted transfer, not an installed weight."""

    @property
    def filename(self) -> str:
        return self.store_rel.rsplit("/", 1)[-1]


class HuggingFaceSource(SchemaModel):
    """A pinned Hugging Face download: ``hf download <repo> [files] --revision <sha>``."""

    repo_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    """A full commit sha, never a branch: ``main`` moves and a moved weight is a different model."""
    files: tuple[str, ...] = ()
    """Exact repo-relative paths. Empty with no ``include`` means the whole repository."""
    include: tuple[str, ...] = ()
    """``--include`` glob patterns, for repos where the wanted subset is a directory."""
    dest_subdir: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9_.-]*)?$")
    """Subdirectory of the family's store dir to download into (the GGUF repos store at root)."""
    strip_prefix: str = Field(default="", pattern=r"^([A-Za-z0-9][A-Za-z0-9_.-]*)?$")
    """Repo prefix to flatten away after the transfer (Comfy-Org's ``split_files/``), so the
    store layout matches what the loaders and the ComfyUI links expect."""
    gated: Gating = "no"
    note: str = Field(default="", max_length=300)


class ReleaseSource(SchemaModel):
    """A pinned release asset fetched over https (upstream publishes no Hub copy)."""

    url: str = Field(min_length=1, max_length=500)
    dest: str = Field(min_length=1, max_length=300)
    """Path under the family's store dir."""

    @model_validator(mode="after")
    def _host_allowed(self) -> ReleaseSource:
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(
            host == h or host.endswith("." + h) for h in RELEASE_HOSTS
        ):
            msg = f"{self.url!r}: release assets must be https on {RELEASE_HOSTS}"
            raise ValueError(msg)
        return self


class WeightPackage(SchemaModel):
    """One installable weight family."""

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,48}$")
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=300)
    """What the pipeline uses it for, in one line — this is what the UI shows, so it says what
    the weight does here rather than what the model card says about it."""
    store_dir: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,64}$")
    license: str = Field(min_length=1, max_length=120)
    approx_bytes: int = Field(ge=0)
    provides: tuple[ProvidedFile, ...] = Field(min_length=1)
    hf: tuple[HuggingFaceSource, ...] = ()
    releases: tuple[ReleaseSource, ...] = ()
    manual: str = Field(default="", max_length=400)
    """Why this family cannot be installed from here, when it cannot."""
    build_command: tuple[str, ...] = ()
    """A local build instead of a download (the Blender asset pack), as an argument array."""
    index_category: str = Field(default="", pattern=r"^([a-z][a-z0-9_]*)?$")
    index_name: str = Field(default="", max_length=80)
    path_aliases: tuple[str, ...] = ()
    """Extra substrings a ``kind="path"`` requirement may identify this family by."""
    stack_key: str = Field(default="", max_length=48)
    """The matching :data:`content_factory.models.video_stack.VIDEO_STACK` entry, where there is
    one, so the two views of a family can be cross-checked by a test rather than by hand."""
    caveat: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _has_a_source(self) -> WeightPackage:
        sources = bool(self.hf or self.releases or self.build_command)
        if sources == bool(self.manual):
            msg = f"{self.key}: needs either sources or a manual reason, not both or neither"
            raise ValueError(msg)
        if bool(self.index_category) != bool(self.index_name):
            msg = f"{self.key}: index_category and index_name go together"
            raise ValueError(msg)
        return self

    @property
    def installable(self) -> bool:
        return not self.manual

    @property
    def gating(self) -> Gating:
        """The strictest gate across this family's sources."""
        if any(s.gated == "manual" for s in self.hf):
            return "manual"
        return "auto" if any(s.gated == "auto" for s in self.hf) else "no"

    def matches_path(self, needle: str) -> bool:
        if not needle:
            return False
        return needle in (self.store_dir, self.key) or needle in self.path_aliases

    def file_named(self, filename: str) -> ProvidedFile | None:
        return next((f for f in self.provides if f.filename == filename), None)


class SkillEnv(SchemaModel):
    """A skill's isolated Python environment: ``uv sync`` in its directory, nothing more."""

    skill: str = Field(pattern=r"^skills/[a-z_]+/[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=300)
    caveat: str = Field(default="", max_length=300)

    @property
    def key(self) -> str:
        return f"skill:{self.skill}"


# --- the registry -------------------------------------------------------------------------------
#
# Revisions: the eleven families the extras script already pinned keep its shas verbatim (verified
# 2026-09-05). The rest were resolved from the Hub API on 2026-09-09 — sha, gating and licence in
# one call per repo — which is also where the sizes come from. Sizes are the sum of the files
# actually fetched, not the repository total: Comfy-Org/SeedVR2 is 113 GB and this stack takes
# 7 GB of it.

WEIGHT_PACKAGES: tuple[WeightPackage, ...] = (
    WeightPackage(
        key="ltx-2.5",
        name="LTX-2.5 22B distilled (Q5_K_M GGUF)",
        purpose="Turns a drawn keyframe into motion — the video model behind generate_video",
        store_dir="ltx25",
        license="other (Lightricks LTX licence; GGUF quants inherit it)",
        approx_bytes=29_200_000_000,
        stack_key="ltx-2.5",
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
                comfy_folder="models/diffusion_models",
                min_bytes=15_000_000_000,
            ),
            ProvidedFile(
                store_rel="text_encoders/gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf",
                comfy_folder="models/text_encoders",
                min_bytes=8_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/ltx-2.5-video-vae-conv-bf16.safetensors",
                comfy_folder="models/vae",
                min_bytes=1_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/ltx-2.5-audio-vae-bf16.safetensors",
                comfy_folder="models/vae",
                min_bytes=200_000_000,
            ),
            # latent_upscale_models, not upscale_models: LatentUpscaleModelLoader reads the former
            # and the link used to point at the latter, which is why it found nothing.
            ProvidedFile(
                store_rel=(
                    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
                ),
                comfy_folder="models/latent_upscale_models",
                min_bytes=500_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="elix3r/LTX-2.5-22b-distilled-GGUF",
                revision="1cd163da90282be382ae5afcb93348a1eed46a88",
                files=("ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",),
                dest_subdir="diffusion_models",
                gated="auto",
            ),
            HuggingFaceSource(
                repo_id="elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF",
                revision="2a18e836d286eb0570ec9f013c3591eb8c614d57",
                files=("gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf",),
                dest_subdir="text_encoders",
                gated="auto",
            ),
            HuggingFaceSource(
                repo_id="Lightricks/LTX-2.5",
                revision="5e6e71018ee1756ed329b697a7b4aedc934dfce9",
                files=(
                    "vae/ltx-2.5-video-vae-conv-bf16.safetensors",
                    "vae/ltx-2.5-audio-vae-bf16.safetensors",
                    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
                ),
                gated="auto",
                note="official VAEs and upscaler; accept the Lightricks terms once",
            ),
        ),
        index_category="video_generation",
        index_name="LTX-2.5",
        caveat=(
            "the gemma GGUF ships no tokenizer metadata, so pure text-to-video fails — this lane"
            " is driven image-to-video from a keyframe"
        ),
    ),
    WeightPackage(
        key="hidream-o1",
        name="HiDream-O1-Image (8B dev checkpoint)",
        purpose="Draws anchors and keyframes — the image model behind generate_anchor",
        store_dir="hidream-o1",
        license="mit",
        approx_bytes=35_200_000_000,
        stack_key="hidream-o1",
        provides=(
            ProvidedFile(store_rel="model-00001-of-00008.safetensors", min_bytes=1_000_000_000),
            ProvidedFile(store_rel="model-00008-of-00008.safetensors", min_bytes=1_000_000_000),
            ProvidedFile(store_rel="model.safetensors.index.json", min_bytes=1_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="HiDream-ai/HiDream-O1-Image",
                revision="0b0901d99f200389e138c61946af1185f5f49a13",
            ),
        ),
        path_aliases=("hidream",),
        index_category="image_generation",
        index_name="HiDream-O1-Image-Dev",
        caveat=(
            "served by this repo's own HiDream skill server, not ComfyUI; local_services"
            " hidream_model_type must stay 'dev' to match these shards"
        ),
    ),
    WeightPackage(
        key="krea2",
        name="Krea 2 Turbo (fp8) + Qwen3-VL text encoder",
        purpose="Styled keyframes through ComfyUI, the alternative anchor backend to HiDream",
        store_dir="krea2",
        license="other (Krea 2 licence)",
        approx_bytes=18_600_000_000,
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/krea2_turbo_fp8_scaled.safetensors",
                comfy_folder="models/diffusion_models",
                min_bytes=10_000_000_000,
            ),
            ProvidedFile(
                store_rel="text_encoders/qwen3vl_4b_fp8_scaled.safetensors",
                comfy_folder="models/text_encoders",
                min_bytes=4_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/qwen_image_vae.safetensors",
                comfy_folder="models/vae",
                min_bytes=200_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="Comfy-Org/Krea-2",
                revision="e5ea8b4dd7f38f348b138eb0fe29f92c0e367e96",
                files=(
                    "diffusion_models/krea2_turbo_fp8_scaled.safetensors",
                    "text_encoders/qwen3vl_4b_fp8_scaled.safetensors",
                    "vae/qwen_image_vae.safetensors",
                ),
            ),
        ),
        index_category="image_generation",
        index_name="Krea2",
        caveat="cannot take control passes as references; generate_anchor refuses it for those",
    ),
    WeightPackage(
        key="flux2-dev",
        name="FLUX.2-dev (Q4_K_M GGUF) + Mistral-Small-3.2 encoder",
        purpose="Multi-reference compositor: what holds one character across frames",
        store_dir="flux2-dev",
        license="other (FLUX.2 dev non-commercial licence)",
        approx_bytes=41_200_000_000,
        stack_key="flux2-dev",
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/flux2-dev-Q4_K_M.gguf",
                comfy_folder="models/diffusion_models",
                min_bytes=15_000_000_000,
            ),
            ProvidedFile(
                store_rel="text_encoders/mistral_3_small_flux2_fp8.safetensors",
                comfy_folder="models/text_encoders",
                min_bytes=12_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/flux2-vae.safetensors",
                comfy_folder="models/vae",
                min_bytes=200_000_000,
            ),
            ProvidedFile(
                store_rel="loras/Flux_2-Turbo-LoRA_comfyui.safetensors",
                comfy_folder="models/loras",
                min_bytes=1_000_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="city96/FLUX.2-dev-gguf",
                revision="ade5d688ddab0d9cf4a5b01bf4321e01c115020d",
                files=("flux2-dev-Q4_K_M.gguf",),
                dest_subdir="diffusion_models",
            ),
            HuggingFaceSource(
                repo_id="Comfy-Org/flux2-dev",
                revision="ab9055628ea245000e610f2aa2c96f4746093546",
                files=(
                    "split_files/text_encoders/mistral_3_small_flux2_fp8.safetensors",
                    "split_files/vae/flux2-vae.safetensors",
                    "split_files/loras/Flux_2-Turbo-LoRA_comfyui.safetensors",
                ),
                strip_prefix="split_files",
                note="flattened out of split_files/ to match the layout the loaders expect",
            ),
        ),
        index_category="image_generation",
        index_name="FLUX.2-dev",
        caveat="35.5 GB of weights against a 24 GB card: the two load in sequence",
    ),
    WeightPackage(
        key="seedvr2-7b",
        name="SeedVR2 7B int8 (upscale/restore)",
        purpose="Restores and upscales a finished frame sequence — the upscale_video stage",
        store_dir="seedvr2-7b",
        license="apache-2.0",
        approx_bytes=8_840_000_000,
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/seedvr2_7b_int8_convrot.safetensors",
                comfy_folder="models/diffusion_models",
                min_bytes=4_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/seedvr2_ema_vae_fp16.safetensors",
                comfy_folder="models/vae",
                min_bytes=100_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="Comfy-Org/SeedVR2",
                revision="673340c8a66db62b84e4099def7d01d337ae12dc",
                files=(
                    "diffusion_models/seedvr2_7b_int8_convrot.safetensors",
                    "vae/seedvr2_ema_vae_fp16.safetensors",
                ),
                note="two files out of a 113 GB repository",
            ),
        ),
        index_category="restoration",
        index_name="SeedVR2-7B",
    ),
    WeightPackage(
        key="seedvr2",
        name="SeedVR2 3B fp16 (upscale/restore, lighter)",
        purpose="The 3B tier of the restorer, for when 8 GB of VRAM is not free",
        store_dir="seedvr2",
        license="apache-2.0",
        approx_bytes=7_280_000_000,
        stack_key="seedvr2",
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/seedvr2_3b_fp16.safetensors",
                comfy_folder="models/diffusion_models",
                min_bytes=4_000_000_000,
            ),
            ProvidedFile(
                store_rel="vae/seedvr2_ema_vae_fp16.safetensors",
                comfy_folder="models/vae",
                min_bytes=100_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="Comfy-Org/SeedVR2",
                revision="673340c8a66db62b84e4099def7d01d337ae12dc",
                files=(
                    "diffusion_models/seedvr2_3b_fp16.safetensors",
                    "vae/seedvr2_ema_vae_fp16.safetensors",
                ),
            ),
        ),
        index_category="restoration",
        index_name="SeedVR2-3B",
    ),
    WeightPackage(
        key="gimm-vfi",
        name="GIMM-VFI (quality frame interpolation)",
        purpose="Fills frames between drawings — the quality tier of the interpolate stage",
        store_dir="gimm-vfi",
        license="unstated on the Hub repo (upstream code is CC BY-NC-SA 4.0)",
        approx_bytes=290_000_000,
        stack_key="gimm-vfi",
        provides=(
            ProvidedFile(store_rel="gimmvfi_r_arb.pt", min_bytes=50_000_000),
            ProvidedFile(store_rel="gimmvfi_f_arb.pt", min_bytes=50_000_000),
            ProvidedFile(store_rel="flowformer_sintel.pth", min_bytes=10_000_000),
            ProvidedFile(store_rel="raft-things.pth", min_bytes=10_000_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="GSean/GIMM-VFI",
                revision="ab7735cdcfbd2e03c1bf2819380a25e8a4f321d1",
                files=(
                    "gimmvfi_f_arb.pt",
                    "gimmvfi_r_arb.pt",
                    "flowformer_sintel.pth",
                    "raft-things.pth",
                ),
            ),
        ),
        index_category="frame_interpolation",
        index_name="GIMM-VFI",
    ),
    WeightPackage(
        key="vfimamba",
        name="VFIMamba",
        purpose="Second interpolation engine, for material GIMM-VFI handles badly",
        store_dir="vfimamba",
        license="apache-2.0",
        approx_bytes=260_000_000,
        stack_key="vfimamba",
        provides=(ProvidedFile(store_rel="model.pkl", min_bytes=1_000_000),),
        hf=(
            HuggingFaceSource(
                repo_id="MCG-NJU/VFIMamba",
                revision="9c6ded6066f6d5cff211e7c466b36dca3137a61d",
            ),
        ),
        index_category="frame_interpolation",
        index_name="VFIMamba",
    ),
    WeightPackage(
        key="rife",
        name="Practical-RIFE 4.25",
        purpose="The fast interpolation tier",
        store_dir="rife",
        license="MIT (code); weights distributed by the author",
        approx_bytes=0,
        provides=(ProvidedFile(store_rel="train_log/flownet.pkl", min_bytes=1_000_000),),
        manual=(
            "upstream publishes the weights on Google Drive and the Hugging Face mirrors are"
            " untrusted zero-download copies. Fetch train_log/ by hand into"
            " <store>/rife/train_log/ — this stays a manual step rather than a pinned lie."
        ),
        index_category="frame_interpolation",
        index_name="Practical-RIFE",
    ),
    WeightPackage(
        key="mmaudio",
        name="MMAudio large 44k v2",
        purpose="Watches the silent cut and writes sound effects onto the frame — sound_design",
        store_dir="mmaudio",
        license="cc-by-nc-4.0",
        approx_bytes=4_200_000_000,
        stack_key="mmaudio",
        provides=(
            ProvidedFile(store_rel="weights/mmaudio_large_44k_v2.pth", min_bytes=500_000_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="hkchengrex/MMAudio",
                revision="eb13a1a98fdbec91753775c57b074ccdfc60587c",
                files=("weights/mmaudio_large_44k_v2.pth",),
                note="weights/ prefix included: the base downloader asked for a bare filename"
                " and left the directory empty",
            ),
        ),
        index_category="sound_effects",
        index_name="MMAudio",
        caveat=(
            "non-commercial licence. MMAudio fetches its shared feature extractors"
            " (synchformer, v1-44) itself on first run"
        ),
    ),
    WeightPackage(
        key="stable-audio-3-sfx",
        name="Stable Audio 3 Small SFX",
        purpose="Text-prompted sound effects, the alternative to watching the cut",
        store_dir="stable-audio-3-small-sfx",
        license="other (Stability AI community licence)",
        approx_bytes=3_500_000_000,
        stack_key="stable-audio-3-sfx",
        provides=(ProvidedFile(store_rel="model.safetensors", min_bytes=100_000_000),),
        hf=(
            HuggingFaceSource(
                repo_id="stabilityai/stable-audio-3-small-sfx",
                revision="ae12755283df9d62ca39a9b050a39a0b607b8c20",
                gated="auto",
            ),
        ),
        index_category="sound_effects",
        index_name="StableAudio3-Small-SFX",
    ),
    WeightPackage(
        key="ace-step-1.5",
        name="ACE-Step 1.5 (Turbo)",
        purpose="Generates music beds for select_music when the local library has no fit",
        store_dir="ace-step-1.5",
        license="mit",
        approx_bytes=10_100_000_000,
        stack_key="ace-step-1.5",
        provides=(
            ProvidedFile(
                store_rel="acestep-5Hz-lm-1.7B/model.safetensors", min_bytes=1_000_000_000
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="ACE-Step/Ace-Step1.5",
                revision="19671f406d603126926c1b7e2adc169acbcade22",
            ),
        ),
        index_category="music",
        index_name="ACE-Step-1.5",
    ),
    WeightPackage(
        key="qwen3-tts-customvoice",
        name="Qwen3-TTS 12Hz 1.7B CustomVoice",
        purpose="The narration voice: synthesize_narration's primary speech model",
        store_dir="qwen3-tts-1.7b-customvoice",
        license="apache-2.0",
        approx_bytes=4_500_000_000,
        stack_key="qwen3-tts-customvoice",
        provides=(
            ProvidedFile(store_rel="model.safetensors", min_bytes=1_000_000_000),
            ProvidedFile(store_rel="speech_tokenizer/model.safetensors", min_bytes=100_000_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                revision="0c0e3051f131929182e2c023b9537f8b1c68adfe",
            ),
        ),
        index_category="speech",
        index_name="Qwen3-TTS-12Hz-1.7B-CustomVoice",
    ),
    WeightPackage(
        key="qwen3-tts-base",
        name="Qwen3-TTS 12Hz 1.7B Base",
        purpose="Voice cloning from a reference take, same family as the narration voice",
        store_dir="qwen3-tts-1.7b-base",
        license="apache-2.0",
        approx_bytes=4_500_000_000,
        stack_key="qwen3-tts-base",
        provides=(
            ProvidedFile(store_rel="model.safetensors", min_bytes=1_000_000_000),
            ProvidedFile(store_rel="speech_tokenizer/model.safetensors", min_bytes=100_000_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                revision="fd4b254389122332181a7c3db7f27e918eec64e3",
            ),
        ),
        index_category="speech",
        index_name="Qwen3-TTS-12Hz-1.7B-Base",
    ),
    WeightPackage(
        key="resemble-enhance",
        name="Resemble Enhance (speech restoration)",
        purpose="Restores a recorded or synthesized take — the enhancer in restore_speech",
        store_dir="resemble-enhance",
        license="mit (code and weights)",
        approx_bytes=710_000_000,
        stack_key="resemble-enhance",
        provides=(
            ProvidedFile(store_rel="enhancer_stage2/hparams.yaml", min_bytes=100),
            ProvidedFile(
                store_rel="enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt",
                min_bytes=100_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="ResembleAI/resemble-enhance",
                revision="4e3510ce4a8391159f665903544c5150bee7b2cb",
                include=("enhancer_stage2/*",),
                note="enhancer_stage2 only; the repo also holds demo videos nothing reads",
            ),
        ),
        index_category="speech_restoration",
        index_name="ResembleEnhance",
    ),
    WeightPackage(
        key="mossformer2-se-48k",
        name="ClearerVoice MossFormer2_SE_48K",
        purpose="Cleans noise and room off a take before restoration — restore_speech cleanup",
        store_dir="mossformer2-se-48k",
        license="apache-2.0",
        approx_bytes=220_000_000,
        stack_key="mossformer2-se-48k",
        provides=(
            ProvidedFile(store_rel="last_best_checkpoint.pt", min_bytes=100_000_000),
            ProvidedFile(store_rel="last_best_checkpoint", min_bytes=10),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="alibabasglab/MossFormer2_SE_48K",
                revision="eff8c97925c8bec812af707814b3e5d777fd4503",
                include=("last_best_checkpoint", "last_best_checkpoint.pt", "README.md"),
            ),
        ),
        index_category="speech_restoration",
        index_name="MossFormer2_SE_48K",
    ),
    WeightPackage(
        key="mossformer2-sr-48k",
        name="ClearerVoice MossFormer2_SR_48K",
        purpose="Extends a narrow-band take to 48 kHz — restore_speech band extension",
        store_dir="mossformer2-sr-48k",
        license="apache-2.0",
        approx_bytes=440_000_000,
        stack_key="mossformer2-sr-48k",
        provides=(
            ProvidedFile(store_rel="last_best_checkpoint_g.pt", min_bytes=100_000_000),
            ProvidedFile(store_rel="last_best_checkpoint_m.pt", min_bytes=100_000_000),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="alibabasglab/MossFormer2_SR_48K",
                revision="39eb1f25ea84f5e0315ade9ac0070fff216fc690",
                include=(
                    "last_best_checkpoint",
                    "last_best_checkpoint_g.pt",
                    "last_best_checkpoint_m.pt",
                    "README.md",
                ),
                note="not do_03925000: that is HiFi-GAN training state no run opens",
            ),
        ),
        index_category="speech_restoration",
        index_name="MossFormer2_SR_48K",
    ),
    WeightPackage(
        key="cutie",
        name="Cutie (tracking + segmentation)",
        purpose="Tracks what fix_video removes; the ungated alternative to SAM 3.1",
        store_dir="cutie",
        license="MIT (code); weight published on the ProPainter release",
        approx_bytes=280_000_000,
        stack_key="cutie",
        provides=(ProvidedFile(store_rel="cutie-base-mega.pth", min_bytes=50_000_000),),
        releases=(
            ReleaseSource(
                url="https://github.com/sczhou/ProPainter/releases/download/v0.1.0/cutie-base-mega.pth",
                dest="cutie-base-mega.pth",
            ),
        ),
        index_category="video_editing",
        index_name="Cutie",
    ),
    WeightPackage(
        key="propainter",
        name="ProPainter (video inpainting)",
        purpose="Paints away what Cutie tracked — the second half of fix_video",
        store_dir="propainter",
        license="S-Lab License 1.0 (NON-COMMERCIAL)",
        approx_bytes=180_000_000,
        stack_key="propainter",
        provides=(
            ProvidedFile(store_rel="ProPainter.pth", min_bytes=100_000_000),
            ProvidedFile(store_rel="raft-things.pth", min_bytes=10_000_000),
            ProvidedFile(store_rel="recurrent_flow_completion.pth", min_bytes=10_000_000),
        ),
        releases=(
            ReleaseSource(
                url="https://github.com/sczhou/ProPainter/releases/download/v0.1.0/ProPainter.pth",
                dest="ProPainter.pth",
            ),
            ReleaseSource(
                url="https://github.com/sczhou/ProPainter/releases/download/v0.1.0/raft-things.pth",
                dest="raft-things.pth",
            ),
            ReleaseSource(
                url="https://github.com/sczhou/ProPainter/releases/download/v0.1.0/recurrent_flow_completion.pth",
                dest="recurrent_flow_completion.pth",
            ),
        ),
        index_category="video_editing",
        index_name="ProPainter",
        caveat="non-commercial licence: check it against how the output is used",
    ),
    WeightPackage(
        key="sam-3.1",
        name="SAM 3.1 (segmentation/tracking)",
        purpose="The quality tracker for fix_video, when Meta approves the access request",
        store_dir="sam3.1",
        license="other (Meta SAM licence)",
        approx_bytes=3_500_000_000,
        stack_key="sam-3.1",
        provides=(ProvidedFile(store_rel="sam3.1_multiplex.pt", min_bytes=100_000_000),),
        hf=(
            HuggingFaceSource(
                repo_id="facebook/sam3.1",
                revision="daa63191845a41281374e725f4c9e51c7a824460",
                gated="manual",
                note="request access on the model page; approval is a human decision at Meta",
            ),
        ),
        index_category="video_editing",
        index_name="SAM-3.1",
    ),
    WeightPackage(
        key="liveportrait",
        name="LivePortrait",
        purpose="Fast face and portrait animation, the speed tier for character shots",
        store_dir="liveportrait",
        license="mit (weights); insightface dependency is non-commercial research",
        approx_bytes=640_000_000,
        stack_key="liveportrait",
        provides=(ProvidedFile(store_rel="liveportrait/landmark.onnx", min_bytes=10_000_000),),
        hf=(
            HuggingFaceSource(
                repo_id="KlingTeam/LivePortrait",
                revision="82a4fa6735ca58432b6ce39301b4b9ee066dea47",
                include=("liveportrait/*",),
                note="official weights moved here from KwaiVGI",
            ),
        ),
        index_category="characters",
        index_name="LivePortrait",
    ),
    WeightPackage(
        key="wan-animate-2",
        name="Wan-Animate-2 14B (community Q5_K_M GGUF)",
        purpose="Character animation from a pose reference — the wan_animate2 package",
        store_dir="wan-animate-2",
        license="apache-2.0 (community quant of an Apache-2.0 base)",
        approx_bytes=12_300_000_000,
        stack_key="wan-animate-2",
        provides=(
            ProvidedFile(
                store_rel="wan_animate_2-Q5_K_M.gguf",
                comfy_folder="models/diffusion_models",
                min_bytes=8_000_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="karcsiha/wan_animate_2_gguf",
                revision="33dc8c561b8ffddab5559c64839ed05c9c7fc8df",
                files=("wan_animate_2-Q5_K_M.gguf",),
                note="community quant: the official repo is BF16-only, ~82 GB, no 24 GB fit",
            ),
        ),
        index_category="characters",
        index_name="Wan-Animate-2",
    ),
    WeightPackage(
        key="wan-2.2-t2v",
        name="Wan 2.2 T2V A14B (Q5_K_M GGUF pair)",
        purpose="The alternative video generator to LTX-2.5, text-to-video capable",
        store_dir="wan2.2",
        license="apache-2.0",
        approx_bytes=29_700_000_000,
        stack_key="wan-2.2-t2v",
        provides=(
            ProvidedFile(
                store_rel="diffusion_models/HighNoise/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf",
                comfy_folder="models/diffusion_models",
                min_bytes=8_000_000_000,
            ),
            ProvidedFile(
                store_rel="diffusion_models/LowNoise/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf",
                comfy_folder="models/diffusion_models",
                min_bytes=8_000_000_000,
            ),
            ProvidedFile(
                store_rel="split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                comfy_folder="models/text_encoders",
                min_bytes=5_000_000_000,
            ),
            ProvidedFile(
                store_rel="split_files/vae/wan2.2_vae.safetensors",
                comfy_folder="models/vae",
                min_bytes=500_000_000,
            ),
        ),
        hf=(
            HuggingFaceSource(
                repo_id="QuantStack/Wan2.2-T2V-A14B-GGUF",
                revision="73eafba53a1a8f29254e4c77f92e74ea27d7cd6f",
                files=(
                    "HighNoise/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf",
                    "LowNoise/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf",
                ),
                dest_subdir="diffusion_models",
            ),
            HuggingFaceSource(
                repo_id="Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                revision="c4f60d30c55a624e35427060fdd217579a6c1d77",
                files=(
                    "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                    "split_files/vae/wan2.2_vae.safetensors",
                ),
            ),
        ),
        index_category="video_generation",
        index_name="Wan2.2-T2V-A14B",
    ),
    WeightPackage(
        key="blender-assets",
        name="Blender character and set assets",
        purpose="Rigged characters and baked pose anchors for the Blender control passes",
        store_dir="blender-assets",
        license="CC0 meshes (MakeHuman) + CMU mocap clips",
        approx_bytes=640_000_000,
        stack_key="blender-characters",
        provides=(
            ProvidedFile(store_rel="clips/manifest.json", min_bytes=10),
            ProvidedFile(
                store_rel="asset-packs/makehuman_system_assets_cc0.zip", min_bytes=1_000_000
            ),
        ),
        build_command=("uv", "run", "python", "-m", "skills.video.blender_scene.assets_build"),
        path_aliases=("blender-assets",),
        index_category="characters",
        index_name="Blender-Assets",
        caveat="built locally rather than downloaded; needs Blender on PATH",
    ),
)


SKILL_ENVS: tuple[SkillEnv, ...] = (
    SkillEnv(
        skill="skills/image/hidream",
        name="HiDream image server",
        purpose="Runs the local HiDream-O1 server that generate_anchor draws through",
        caveat="torch 2.8 plus a prebuilt flash-attn wheel; the first sync is slow",
    ),
    SkillEnv(
        skill="skills/audio/qwen3tts",
        name="Qwen3-TTS voice",
        purpose="Synthesizes narration with word timings",
    ),
    SkillEnv(
        skill="skills/audio/kokoro",
        name="Kokoro voice",
        purpose="The lighter alternative narration voice",
    ),
    SkillEnv(
        skill="skills/audio/clearervoice",
        name="ClearerVoice cleanup",
        purpose="Noise/room cleanup and 48 kHz band extension inside restore_speech",
    ),
    SkillEnv(
        skill="skills/audio/resemble_enhance",
        name="Resemble Enhance",
        purpose="Speech restoration inside restore_speech",
    ),
    SkillEnv(
        skill="skills/audio/sfx",
        name="Sound-effects library",
        purpose="Indexes and serves the local SFX library",
    ),
    SkillEnv(
        skill="skills/video/postchain",
        name="Post chain (interpolate, upscale, inpaint)",
        purpose="Runs GIMM-VFI, SeedVR2, Cutie and ProPainter out of one env",
    ),
    SkillEnv(
        skill="skills/video/blender_scene",
        name="Blender scene controller",
        purpose="Builds the control passes plan_shots and compile_controls need",
    ),
    SkillEnv(
        skill="skills/video/manim",
        name="Manim scenes",
        purpose="Renders the opt-in Manim explainer animations",
    ),
)


def package_by_key(key: str) -> WeightPackage | None:
    return next((p for p in WEIGHT_PACKAGES if p.key == key), None)


def skill_env_by_key(key: str) -> SkillEnv | None:
    return next((s for s in SKILL_ENVS if s.key == key or s.skill == key), None)


def package_for_requirement(req: ModelRequirement) -> WeightPackage | None:
    """The family that satisfies a declared workflow requirement, or None when nothing here does.

    A ``comfy`` requirement names a file and the ComfyUI folder it loads from, so it matches on
    both; a ``path`` requirement names a directory substring and usually a file inside it.
    """
    if req.kind == "comfy":
        wanted_folder = f"models/{req.folder}"
        for package in WEIGHT_PACKAGES:
            provided = package.file_named(req.filename)
            if provided is not None and provided.comfy_folder in ("", wanted_folder):
                return package
        return None
    if req.kind == "path":
        for package in WEIGHT_PACKAGES:
            if not package.matches_path(req.path_includes):
                continue
            return package
        # No directory match: fall back to the filename, which is enough to be unambiguous for
        # the families that declare one.
        if req.filename:
            for package in WEIGHT_PACKAGES:
                if package.file_named(req.filename) is not None:
                    return package
    return None
