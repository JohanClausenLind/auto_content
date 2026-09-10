"""Typed configuration (section 26 of the program).

Precedence (highest wins): environment variables > YAML config file > model defaults.
Immutable safety policy is *code*, not config, and cannot be weakened from here.

Environment overrides use the ``CF__`` prefix with ``__`` as the nesting delimiter,
e.g. ``CF__BUDGETS__MONTHLY_EXTERNAL_USD=40``.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

CONFIG_FILE_ENV = "CF_CONFIG_FILE"
DEFAULT_CONFIG_FILE = "content-factory.yaml"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthMode(StrEnum):
    local = "local"
    oidc = "oidc"
    saml = "saml"


class TailscaleMode(StrEnum):
    off = "off"
    serve = "serve"
    funnel = "funnel"


class ExecutionPolicyKind(StrEnum):
    cloud_only = "cloud_only"
    quality_auto = "quality_auto"
    prefer_local = "prefer_local"
    local_only = "local_only"
    pinned = "pinned"


class AuthSettings(StrictModel):
    mode: AuthMode = AuthMode.local
    passkeys_enabled: bool = True
    totp_enabled: bool = True
    require_step_up_for_sensitive_actions: bool = True
    tailscale_identity: bool = False
    session_ttl_hours: int = Field(default=24 * 14, ge=1)
    # Minimum accepted password length. Default follows OWASP; the schema floors it at 4 so a
    # single-operator install may relax it deliberately, never accidentally to nothing.
    password_min_length: int = Field(default=12, ge=4, le=128)
    step_up_ttl_minutes: int = Field(default=10, ge=1)
    # WebAuthn relying party identity. rp_id must be the registrable domain of public_base_url.
    rp_id: str = "localhost"
    rp_name: str = "Content Factory"


class TailscaleSettings(StrictModel):
    mode: TailscaleMode = TailscaleMode.off
    funnel_requires_password_auth: bool = True


class DatabaseSettings(StrictModel):
    url_env: str = "DATABASE_URL"
    # Resolved at runtime from the named environment variable; never stored in YAML.
    pool_size: int = Field(default=10, ge=1)

    def url(self) -> SecretStr:
        raw = os.environ.get(self.url_env)
        if not raw:
            msg = f"Database URL environment variable {self.url_env!r} is not set"
            raise RuntimeError(msg)
        return SecretStr(raw)


class ObjectStoreSettings(StrictModel):
    backend: Literal["filesystem", "s3"] = "filesystem"
    bucket: str = "./data/artifacts"
    endpoint_url: str | None = None
    region: str | None = None
    access_key_env: str = "OBJECT_STORE_ACCESS_KEY"
    secret_key_env: str = "OBJECT_STORE_SECRET_KEY"  # noqa: S105 (env var name)


class TokenVaultSettings(StrictModel):
    master_key_env: str = "VAULT_MASTER_KEY"
    refresh_lock_seconds: int = Field(default=60, ge=1)


class TemporalSettings(StrictModel):
    address: str = "127.0.0.1:7233"
    namespace: str = "content-factory-dev"
    task_queues: tuple[str, ...] = (
        "control",
        "research",
        "render-cpu",
        "render-gpu",
        "inference-llm",
        "inference-image",
        "inference-audio",
        "publish",
        "radar",
        "imports",
    )


class ThemeSettings(StrictModel):
    default_preset: str = "dark"
    allow_custom_themes: bool = True


class BudgetSettings(StrictModel):
    monthly_external_usd: float = Field(default=40.0, ge=0)
    per_project_external_usd: float = Field(default=10.0, ge=0)
    warn_ratio: float = Field(default=0.7, gt=0, le=1)
    hard_stop: bool = True


class ExecutionSettings(StrictModel):
    default_policy: ExecutionPolicyKind = ExecutionPolicyKind.quality_auto
    allow_cloud_fallback: bool = True
    # Draft copy with the local model catalog (qwen38-ridge via Ollama) instead of fixtures.
    # Off by default so offline runs and tests stay deterministic.
    local_copywriter: bool = False
    # Run the real search -> fetch -> extract -> claim pipeline (research/pipeline.py) instead of
    # the committed fixture. Off by default because it is the ONE thing in this repo that reaches
    # the public internet, and `just test` must not: every SSRF guard, content-type allowlist and
    # injection flag in research/fetch.py exists for what this switch turns on.
    live_research: bool = False
    # Draft the StoryPlan's beats AND typed scenes with the local model (models/scriptwriter.py)
    # instead of loading a fixture. Off by default and staying off until the evaluation pack
    # scores a build: a writer whose output nobody has scored is not a default.
    local_scriptwriter: bool = False
    minimum_vram_headroom_ratio: float = Field(default=0.12, ge=0, lt=1)
    calibration_max_age_days: int = Field(default=30, ge=1)


class AnthropicProviderSettings(StrictModel):
    api_key_env: str = "ANTHROPIC_API_KEY"


class OpenAICompatibleEndpoint(StrictModel):
    name: str
    base_url: str
    api_key_env: str | None = None


class OllamaSettings(StrictModel):
    endpoint: str = "http://127.0.0.1:11434"


class ProvidersSettings(StrictModel):
    anthropic: AnthropicProviderSettings = AnthropicProviderSettings()
    openai_compatible: tuple[OpenAICompatibleEndpoint, ...] = ()
    ollama: OllamaSettings = OllamaSettings()


class WorkflowLabSettings(StrictModel):
    enabled: bool = True
    disposable_sandbox: bool = True
    requires_promotion_review: bool = True


class MediaLibrarySettings(StrictModel):
    """Local libraries of reusable assets (music beds today). Directories are read-only inputs;
    each library carries a manifest whose entries are validated contracts."""

    music_dir: str = "fixtures/music"
    # The curated sound library: 670 loudness-measured, provenance-tracked sounds under
    # assets/sfx, indexed by assets/sfx/manifest.json. Read-only, like the music library.
    sfx_dir: str = "assets/sfx"


class GallerySettings(StrictModel):
    """Where a finished film is put so a person can find it.

    A run's deliverable lives at
    ``output/<project>/deliverables/<id>/exports/final.mp4`` — correct, addressable, and no use to
    anybody browsing. 213 run directories held 34 films between them and the only way to watch one
    was to know the path. So the last stage of a run also publishes the film into one flat
    directory, named by the run, and that directory is the answer to "where are the videos".

    Hard-linked, not copied: same filesystem, no second copy of the bytes, and deleting the
    gallery cannot lose anything. ``dir`` empty turns it off.
    """

    dir: str = "videos"


class AnimationSettings(StrictModel):
    # builtin: deterministic Pillow renderer (no extra deps). manim: the opt-in skill env at
    # skills/video/manim with full mathematical typesetting.
    executor: Literal["builtin", "manim"] = "builtin"


class ComfyUISettings(StrictModel):
    enabled: bool = True
    managed_by_comfy_cli: bool = True
    workspace: str = "./.comfy"
    endpoint: str = "http://127.0.0.1:8188"
    production_workflows_only: bool = True
    allow_arbitrary_custom_nodes: bool = False
    # Extra directories the model inventory lists (skill weights, shared model stores). Scanned
    # read-only; nothing outside these and the workspaces is ever touched.
    extra_model_roots: tuple[str, ...] = ()
    workflow_lab: WorkflowLabSettings = WorkflowLabSettings()


class VideoSettings(StrictModel):
    """generate_video: which backend animates the anchors and how LTX-2.5 is driven."""

    # mock: deterministic ffmpeg clip. comfyui: LTX-2.5 i2v packages on the local ComfyUI
    # (started with --cache-none --reserve-vram 1.5 for the GGUF stack).
    backend: Literal["mock", "comfyui"] = "mock"
    # ltx-2.5.i2v: first frame + anchor keyframe guides. wan-animate-2.pose: the anchor as the
    # reference character driven by the Blender OpenPose skeleton video (needs clip_vision_h +
    # wan_2.1_vae in the ComfyUI model dirs).
    package: Literal["ltx-2.5.i2v", "wan-animate-2.pose"] = "ltx-2.5.i2v"
    # How the stills become moving picture. ltx: a generative model invents the motion between
    # them. hold: each drawing is simply held for its shot's duration and the shots are cut
    # together — no model, no blending, no interpolation, and the cuts stay visible. That jump
    # between drawings is a deliberate look (limited animation, stop motion), not a defect, and it
    # is the only mode that guarantees every frame on screen is a drawing you approved.
    motion: Literal["ltx", "hold"] = "ltx"
    default_size: tuple[int, int] = (512, 896)
    default_duration_s: float = Field(default=3.0, ge=1.0, le=30.0)
    fps: Literal[24, 25, 30] = 24
    # Strength of each HiDream anchor used as an LTXVAddGuide keyframe (0 = ignore, 1 = pin).
    guide_strength: float = Field(default=1.0, ge=0.0, le=10.0)
    max_guides: int = Field(default=3, ge=0, le=4)
    # --cache-none suppresses websocket output events, so poll /history by default.
    collect: Literal["history", "websocket"] = "history"
    timeout_s: int = Field(default=1800, ge=60)


class PostChainSettings(StrictModel):
    """fix_video / upscale_video / interpolate: tools behind skills/video/postchain."""

    # fix_video removes these Blender segmentation ids (tracked by Cutie, inpainted by ProPainter);
    # empty = the stage records "nothing to remove" and passes frames through.
    remove_seg_ids: tuple[int, ...] = ()
    mask_dilation: int = Field(default=4, ge=0, le=64)
    upscaler: Literal["seedvr2"] = "seedvr2"
    upscale_resolution: Literal[720, 1080, 1440, 2160] = 1080
    # none: no interpolation at all — the frames are muxed as they are. Choose it when the
    # choppiness is the point; smoothing a held-drawing cut destroys exactly the look it has.
    interpolator: Literal["rife", "gimm_vfi", "none"] = "rife"
    interpolate_factor: Literal[2, 4] = 2
    timeout_s: int = Field(default=7200, ge=60)


class SearchSettings(StrictModel):
    provider: Literal["searxng", "commercial", "fixture"] = "searxng"
    # Default avoids 8080 because other stacks commonly occupy it; compose maps 8083 -> 8080.
    endpoint: str = "http://127.0.0.1:8083"
    offline_fixture_fallback: bool = True


class ResearchSettings(StrictModel):
    citations_required: bool = True
    freshness_days: int = Field(default=30, ge=1)


class RadarSettings(StrictModel):
    enabled: bool = False
    sources: tuple[str, ...] = ()


class PreflightSettings(StrictModel):
    required_before_expensive_execution: bool = True


class ApprovalsSettings(StrictModel):
    default_policy: Literal["solo_operator", "multi_stage"] = "solo_operator"


class OriginalitySettings(StrictModel):
    enabled: bool = True
    compare_scopes: tuple[Literal["account", "workspace", "content_family"], ...] = (
        "account",
        "workspace",
        "content_family",
    )
    recheck_before_publication: bool = True
    block_mass_production_risk: bool = True


DELIVERABLE_TYPES = (
    "long_video",
    "short_video",
    "single_image_post",
    "carousel",
    "infographic",
    "text_post",
    "thread",
    "article",
    "newsletter",
    "email_campaign",
    "audio_clip",
    "audiogram",
    "cover",
    "image_sequence",
)


class DeliverablesSettings(StrictModel):
    allowed_types: tuple[str, ...] = DELIVERABLE_TYPES
    require_explicit_selection: bool = True
    never_add_unselected_destinations: bool = True

    @model_validator(mode="after")
    def _known_types(self) -> DeliverablesSettings:
        unknown = set(self.allowed_types) - set(DELIVERABLE_TYPES)
        if unknown:
            msg = f"Unknown deliverable types: {sorted(unknown)}"
            raise ValueError(msg)
        return self


class DistributionSettings(StrictModel):
    enabled: bool = False
    kill_switch: bool = True
    default_mode: Literal["disabled", "package_only", "draft", "scheduled", "automatic"] = (
        "package_only"
    )
    refresh_capabilities_before_publish: bool = True


class ShortLinkSettings(StrictModel):
    provider: Literal["none", "shlink"] = "none"
    endpoint: str | None = None
    api_key_env: str = "SHLINK_API_KEY"


class GA4Settings(StrictModel):
    enabled: bool = False


class ShopifyWebhookSettings(StrictModel):
    enabled: bool = False


class AttributionSettings(StrictModel):
    utm_enabled: bool = True
    short_links: ShortLinkSettings = ShortLinkSettings()
    ga4: GA4Settings = GA4Settings()
    shopify_webhooks: ShopifyWebhookSettings = ShopifyWebhookSettings()


class PersonaSettings(StrictModel):
    enabled: bool = True
    # Immutable in practice: the firewall is enforced in code regardless of this value.
    firewall_required: Literal[True] = True
    default_disclosure: Literal["disclose_on_ask", "deflect"] = "disclose_on_ask"


class PortalSettings(StrictModel):
    enabled: bool = True
    # Signing secret is resolved from this environment variable at request time (never stored).
    secret_env: str = "CF_PORTAL_SECRET"  # noqa: S105 - the env var NAME, not a secret
    default_ttl_days: int = Field(default=30, ge=1, le=365)


class EngagementSettings(StrictModel):
    enabled: bool = False
    default_autonomy: Literal["draft_only", "human_approve_each", "auto_send_low_risk"] = (
        "human_approve_each"
    )
    auto_send_hourly_cap: int = Field(default=12, ge=0)
    answer_everything: bool = True


class HumanTaskSettings(StrictModel):
    enabled: bool = True
    asr_script_match_tolerance: float = Field(default=0.85, ge=0, le=1)
    reminder_hours: tuple[int, ...] = (24, 4)


class StyleExplorationSettings(StrictModel):
    enabled: bool = True
    default_exploration_rate: float = Field(default=0.1, ge=0, le=1)
    min_samples_before_adopt: int = Field(default=5, ge=1)
    retest_cooldown_days: int = Field(default=21, ge=0)


class DriftThresholdSet(StrictModel):
    """An override of one or both drift thresholds. Both fields are optional so an override can
    say only what it means: a style has an opinion about global variation and a camera move has
    one about composition, and neither should have to restate the other's number."""

    locked_region_similarity: float | None = Field(default=None, ge=0, le=1)
    style_delta_max: float | None = Field(default=None, ge=0, le=1)


class DriftThresholds(StrictModel):
    """How far a generated frame may drift from its anchor, with per-style and per-camera
    overrides.

    One global pair was never right for twelve art directions and nine camera moves. A watercolour
    wash legitimately varies more between frames than a photograph does, and a slow push-in
    legitimately moves the whole composition where a static shot does not — so a single
    `locked_region_similarity` either fails honest frames in one style or passes drifting ones in
    another. `scripts/generate_holding_hands.py` had already discovered this the hard way and
    carried `0.30`/`0.60` inline with the comment "calibrate before tightening", which is a
    calibration living in a script instead of in configuration.

    **The tables start empty on purpose.** An override has to come from a measured run; inventing
    per-style numbers here would be the same mistake as the hardcoded pair, with more places to
    look for it. `sequences.drift.UNCALIBRATED` is the named profile for a first real-model run.
    """

    locked_region_similarity: float = Field(default=0.92, ge=0, le=1)
    style_delta_max: float = Field(default=0.15, ge=0, le=1)
    # Keyed by a `sequences.styles.STYLE_PRESETS` name.
    by_style: dict[str, DriftThresholdSet] = Field(default_factory=dict)
    # Keyed by a `schemas.shots.CameraPreset` value.
    by_camera: dict[str, DriftThresholdSet] = Field(default_factory=dict)

    def resolve(self, *, style: str = "", camera: str = "") -> tuple[float, float]:
        """The (locked_region_similarity, style_delta_max) pair in force for one frame.

        Layered per field: the default, then the camera override, then the style override. Style
        last because it is the more specific art direction — a camera move is a fact about the
        shot, a style is a decision about the film.
        """
        locked = self.locked_region_similarity
        delta = self.style_delta_max
        for override in (self.by_camera.get(camera), self.by_style.get(style)):
            if override is None:
                continue
            if override.locked_region_similarity is not None:
                locked = override.locked_region_similarity
            if override.style_delta_max is not None:
                delta = override.style_delta_max
        return locked, delta


class ImageSequenceSettings(StrictModel):
    enabled: bool = True
    # Which reference-edit backend generates anchors and keyframes: mock (deterministic Pillow
    # stand-in) or hidream (the loopback skill server at skills/image/hidream).
    backend: Literal["mock", "hidream", "flux2"] = "mock"
    hidream_endpoint: str = "http://127.0.0.1:8801"
    hidream_endpoints: tuple[str, ...] = ()
    """Every HiDream server to spread a sequence's frames over, one per GPU host.

    Empty keeps the single ``hidream_endpoint`` above and the serial path, byte for byte. When set
    this REPLACES it rather than adding to it, so a host that should take a share of the work has
    to appear in the list -- including the local one. Frames are hub-and-spoke (each reads the
    anchor, never its neighbour) and each carries its own ``input_hash`` marker, so which host
    serves which frame changes the wall clock and nothing else."""
    # flux2 is the multi-reference compositor and it is a different job from hidream, not a
    # replacement: measured over thirty staged anchors, hidream held one world across every frame
    # (consecutive-frame churn 9.6-19.8 against a threshold of 42) and changed the character's
    # outfit four times inside it. One reference is a pose hint; identity needs several composed
    # at once, which is what flux2's chained ReferenceLatent conditioning does. It is driven
    # through the local ComfyUI, like LTX-2.5, rather than through a bespoke server.
    flux2_endpoint: str = "http://127.0.0.1:8188"
    flux2_endpoints: tuple[str, ...] = ()
    """The flux2 equivalent of ``hidream_endpoints``. These are ComfyUI hosts, so a pool here and
    in :class:`ComfyUISettings` should normally name the same machines."""
    flux2_turbo: bool = True
    """The Turbo LoRA, which is what makes 8 steps enough. Off means 28 steps at guidance 4.0 and
    roughly three times the wall clock for a 4 MP frame on a 24 GB card."""
    # Provisional GenerationLock for anchors (the lock stage freezes these plus the anchor sha).
    anchor_seed: int = Field(default=7, ge=0)
    # 28 steps at guidance 0, which is the *dev* recipe. These were 50 and 5.0 — the full model's
    # recipe — left behind when hidream_model_type moved to "dev", so the lane was overriding the
    # server's correct choice with the wrong one. It cost three times the GPU time per anchor
    # (6 min 25 s against 114 s for the same request at the server's own default) and, since an
    # explicit step count also drops the distilled timestep schedule, it was not the recipe any of
    # this phase's measurements were taken on. Guidance 0 is not a tuning choice either: the dev
    # weights are distilled not to need it, and the full model's 5.0 is what the two recipes
    # differ by.
    anchor_steps: int = Field(default=28, ge=1, le=200)
    anchor_guidance: float = Field(default=0.0, ge=0)
    anchor_sampler: str = "flow_match"
    """Only reaches the server for a *single*-reference request, where it selects between upstream's
    two editing schedulers. With two or more references the count decides and the server picks
    ``flash`` — see anchor_references."""
    anchor_model_revision: str = "HiDream-O1-Image"
    # Which control passes are sent to the image model as references, in order. HiDream-O1's IP
    # pipeline treats *every* reference as subject material, so this is not a free knob: feeding it
    # the Blender clay render makes it draw clay people, and feeding it an untextured MPFB
    # turnaround as an identity reference makes it draw a nude mannequin.
    #
    # "identity" is now buildable. `controls/identity_sheets.py` draws a styled, clothed sheet per
    # character per style from the asset's own turnaround views, `review_assets` gates it beside
    # the mesh, and `_identity_reference` sends that and never the clay render. It is still NOT in
    # the default list, and deliberately: adding it makes every anchor a three-reference request,
    # which is a different editing recipe upstream and a third of the reference budget, and the
    # comparison against the measured two-reference recipe below has not been run. Turn it on per
    # lane (the generate_anchor `references` value) when the measurement says to.
    #
    # It is also, upstream, a recipe selector, and that is the bigger effect: inference.py
    # branches on `len(ref_images) == 1`, sending one reference to the flow_match editing recipe
    # and anything else to flash. Measured on one staged runner shot, same seed, same prompt:
    #
    #   skeleton alone (flow_match)       0.180 crushed  0.293 midtones  0.106 hi-freq  pose ok
    #   skeleton + depth (flow_match)     0.176          0.460           0.175          pose ok
    #   skeleton + depth (flash)          0.001          0.990           0.0004         pose ok
    #
    # The last row is the one to ship: a clean photographic frame with the staged stride and none
    # of the high-frequency speckle the flow_match integration leaves on flat surfaces. Depth
    # earns its place twice over - it is a smooth grey figure with no saturated colour in it, so
    # the model reads it as structure rather than drawing it the way it will draw the skeleton's
    # bright dots, and adding it is what moves this off the single-reference editing recipe.
    anchor_references: tuple[str, ...] = ("pose_skeleton", "depth")
    anchor_style_prompt: str = (
        "premium practical-film photography, natural light, physically convincing materials"
    )
    anchor_lighting_prompt: str = "soft directional key light"
    anchor_background_prompt: str = "as staged in the scene"
    # Where sequence runs write their workdirs; the review API serves files from here only.
    output_root: str = "output"
    default_working_resolution: tuple[int, int] = (1024, 576)
    default_final_resolution: tuple[int, int] = (1536, 864)
    hub_and_spoke_only: bool = True
    max_chain_depth: int = Field(default=1, ge=0)
    drift_thresholds: DriftThresholds = DriftThresholds()
    max_regen_attempts_per_frame: int = Field(default=3, ge=1)
    consistency_priority: tuple[str, ...] = (
        "composition",
        "subject_anatomy",
        "style",
        "fine_detail",
    )

    def hidream_pool(self) -> tuple[str, ...]:
        """Every HiDream endpoint to spread frames over, in order, never empty."""
        return self.hidream_endpoints or (self.hidream_endpoint,)

    def flux2_pool(self) -> tuple[str, ...]:
        """Every flux2 (ComfyUI) endpoint to spread frames over, in order, never empty."""
        return self.flux2_endpoints or (self.flux2_endpoint,)


class ReferenceSettings(StrictModel):
    """The reference library: where the real human-interaction material lives, and how much of it
    a retrieval stage asks for.

    The library is host-specific and lives outside the repo for the same reason model weights do.
    An absent library is not an error: ``find_reference`` selects nothing and says so, and every
    lane still runs.
    """

    root: str = "/mnt/fast/reference"
    index_path: str = "/mnt/fast/reference/_index/index.sqlite"
    lexicon_path: str = "fixtures/reference/lexicon.v1.json"
    default_limit: int = Field(default=10, ge=1, le=100)
    # None means no constraint. Set to pose_derivable to refuse material whose poses cannot drive
    # a shot, which is what a lane that stages from mocap wants.
    require_usage: str | None = None
    require_affection: str | None = None
    """Set to 'affection' on a tender lane so a shove can never be retrieved for a comfort scene."""


class ShotSettings(StrictModel):
    """Shot planning for the 3D scene-control layer (plan_shots stage)."""

    # story_presets: one shot per story beat with a camera preset chosen by scene kind.
    # fixture: load a hand-authored ShotPlan from ``fixture_path`` (repo-relative).
    planner: Literal["story_presets", "fixture", "reference"] = "story_presets"
    fixture_path: str = "fixtures/shots/demo.json"
    shot_size: tuple[int, int] = (1024, 576)
    fps: Literal[24, 25, 30, 60] = 24
    # LTX-2.5 generates 8k+1 frames; snapping here keeps anchors on valid frame indices.
    snap_to_ltx_length: bool = True


class ControlSettings(StrictModel):
    """Which compiler produces the per-frame control passes (compile_controls stage)."""

    # motion_plan: deterministic Pillow renderer from a 2D MotionPlan (no extra deps).
    # blender: headless Blender scene controller at skills/video/blender_scene.
    compiler: Literal["motion_plan", "blender"] = "motion_plan"
    blender_bin: str = "blender"
    engine: Literal["workbench", "eevee", "cycles_cpu"] = "workbench"
    assets_root: str = "/mnt/fast/models/blender-assets"
    timeout_s: int = Field(default=1800, ge=60)
    asset_approvals_dir: str = "output/asset-reviews/approvals"
    """Where ``content-factory assets approve`` writes and ``review_assets`` reads.

    Deliberately not inside a run's project dir. An approval binds to the built mesh's digest, so
    it is a standing statement about that sculpture rather than a fact about one run; keeping it
    per-run would demand a fresh approval of an unchanged mesh for every film, which is how a
    review gate turns into a rubber stamp."""


class RoutingSettings(StrictModel):
    """route_shots stage (hybrid workflow): which story beats the deterministic renderer draws
    (Remotion: D3 / Vega-Lite / MapLibre / Manim scenes) and which the generative chain produces
    (Blender controls -> HiDream anchors -> LTX-2.5). compose_video interleaves both, in beat
    order."""

    # Route for beats whose scene kind is in neither list.
    default_route: Literal["render", "generate"] = "render"
    # Scene kinds whose beats go to the generative chain. Exactly one, and the reason is what the
    # rendered films showed rather than what the taxonomy suggested.
    #
    # This list used to hold title, section_intro, chapter_transition, image, quote, callout and
    # outro, on the theory that a beat which "sets a scene" has no single correct picture. Six of
    # those seven are TEXT scenes: their whole content is words on screen — a title, a label and a
    # heading, a pull quote with its attribution, a call to action. A card renderer sets those in
    # the pinned face at the pinned size and gets them right every run; an image model renders
    # typography as ornament that looks like letters, and every wind short v1-v5 shows it. Worse,
    # a quote is the one scene where the words are a claim attributed to a named person: a
    # generative pass over it is a fabrication risk, not a style choice.
    #
    # ``image`` is the kind with no text in it at all. It names an asset and an alt text, so there
    # is nothing for the card renderer to typeset and nothing for the image model to garble. That
    # makes it the only default. Anything else is a per-lane or per-beat decision, made in the
    # lane's yaml or in ``overrides``, where it is written down.
    generate_kinds: tuple[str, ...] = ("image",)
    # Per-beat overrides by beat id, e.g. CF__ROUTING__OVERRIDES='{"beat_000000002": "generate"}'.
    overrides: dict[str, Literal["render", "generate"]] = {}


class ComposeSettings(StrictModel):
    """compose_video: burned-in captions. Most short-form viewing is muted, so the words go on the
    picture, inside the platform safe zone (clear of the bottom UI band and the right-hand
    button column), not only in the sidecar .srt."""

    burn_captions: bool = True
    # The caption face is the same family as the cards' body text: Inter Bold, from the pinned
    # TTFs under assets/fonts/inter (SIL OFL 1.1). libass reads that directory directly, so no
    # system font install is needed and the render is the same on every machine.
    caption_font: str = "Inter"
    caption_fonts_dir: str = "assets/fonts"
    # Word-by-word highlight: the word being spoken takes the accent colour (restrained: colour
    # only, no scale or bounce). Falls back to plain cues when the word timings are missing.
    caption_highlight: bool = True
    caption_highlight_color: str = Field(default="#8BBDEB", pattern=r"^#[0-9a-fA-F]{6}$")
    # StoryPlan.hook_text is burned as a headline over the opening seconds (Sora Bold, top third).
    hook_overlay: bool = True
    hook_seconds: float = Field(default=2.8, ge=0.5, le=10.0)
    hook_font: str = "Sora"
    hook_size_frac: float = Field(default=0.0711, ge=0.02, le=0.12)
    hook_top_frac: float = Field(default=0.14, ge=0.03, le=0.5)
    # Text sizes are fractions of the frame's SHORTER SIDE, so one number is one physical size in
    # both orientations. They were fractions of the height, and every one of them was calibrated
    # on a 1080x1920 vertical frame — so a 16:9 film got captions at 32 px and a headline at 43 px,
    # 55 % of their intended size. 0.0533 x 1080 is the same 58 px that 0.03 x 1920 was.
    caption_size_frac: float = Field(default=0.0533, ge=0.02, le=0.1)
    # A position, not a size, so still a fraction of the height: 0.22 keeps the block above the
    # ~320 px platform UI band on a 1920 px phone frame.
    caption_bottom_frac: float = Field(default=0.22, ge=0.05, le=0.5)
    # Cue text is re-wrapped to this many characters per line before burning, so the block stays
    # two or three short lines inside the safe width on any background. 0 derives it from the
    # frame — 22 on a phone, 39 on 16:9 — because a fixed 22 makes a landscape caption a line one
    # third of the frame wide, stacked four deep.
    caption_max_chars_per_line: int = Field(default=0, ge=0, le=48)
    # Semi-transparent box behind white text reads on cream cards and on dark footage alike.
    caption_box_alpha: float = Field(default=0.55, ge=0.0, le=1.0)


class NarrationSettings(StrictModel):
    """synthesize_narration: which TTS speaks the locked script, and how it is timed.

    mock: deterministic tone bursts (tests, offline demo). qwen3tts: the narration voice since
    2026-09-07 — ``skills/audio/qwen3tts`` in its own uv environment, nine built-in timbres, style
    control, ten languages, Apache-2.0 weights. kokoro: the fallback, 82M parameters, and the only
    one whose word timings come from the model itself.
    """

    tts: Literal["mock", "qwen3tts", "kokoro"] = "mock"

    # ---- Qwen3-TTS ---------------------------------------------------------------------------
    # A CustomVoice timbre: ryan | aiden | vivian | serena | uncle_fu | dylan | eric | ono_anna |
    # sohee. `run.py --list` prints what the downloaded weights actually declare.
    qwen_speaker: str = Field(default="ryan", min_length=1, max_length=80)
    qwen_language: str = Field(default="english", min_length=1, max_length=32)
    # Natural-language delivery note, e.g. "Calm documentary narrator, unhurried." Empty = none.
    qwen_instruct: str = Field(default="", max_length=500)
    # ~5 GB at bf16, so unlike HiDream/LTX it does not need the exclusive-GPU dance. CPU works but
    # is far slower than Kokoro's, since this is 1.7B parameters, not 82M.
    qwen_device: str = Field(default="cuda:0", min_length=1, max_length=32)
    # Voice clone on the Base weights instead of a built-in timbre: both must be set together.
    # The path is repo-relative unless absolute.
    qwen_ref_audio: str = ""
    qwen_ref_text: str = Field(default="", max_length=2000)
    # Qwen3-TTS samples, so the same beat spoken twice differs audibly and by tens of milliseconds
    # in length. A fixed seed makes a take a fact about its inputs (and is part of the cache key,
    # so changing it re-speaks), which is what has to be true before any retry loop exists: a
    # retry that cannot reproduce the take it is retrying is a dice roll, not a retry.
    # None lets the model sample freely — for deliberately auditioning several reads of one line.
    qwen_seed: int | None = 7
    tts_timeout_s: int = Field(default=900, ge=30, le=86400)

    # ---- timing (Qwen3-TTS returns none of its own; ADR-0004 precedence) ---------------------
    # faster_whisper: measured, the default for a real run. even_split: apportions the measured
    # duration by word length, offline, and records itself as `estimated`, never as measured.
    aligner: Literal["even_split", "faster_whisper", "whisperx"] = "faster_whisper"
    faster_whisper_model: str = "base.en"
    faster_whisper_compute_type: str = "int8"
    aligner_timeout_s: int = Field(default=600, ge=10, le=7200)
    # Below this transcript similarity the beat failed to say the locked script and the stage says
    # so. Lower than the recorded-take floor (0.85): a TTS reads normalized text, so the aligner's
    # transcript legitimately differs more around numbers and units.
    script_similarity_min: float = Field(default=0.8, ge=0.0, le=1.0)
    # How many times a beat may be spoken before the gate above is treated as the script's fault
    # rather than the sample's. Qwen3-TTS is a sampling model and it drops material: one beat of a
    # curveball explainer came back as its second sentence alone (similarity 0.76) and failed a
    # fourteen-stage run at stage four, and the next sample said the whole thing. Three takes, each
    # reseeded; the cost is only paid by beats that actually failed.
    tts_takes: int = Field(default=3, ge=1, le=6)

    # ---- Kokoro (fallback) -------------------------------------------------------------------
    kokoro_voice: str = Field(default="af_heart", min_length=1, max_length=80)
    # Kokoro is 82M parameters: CPU is a few seconds per beat and never fights HiDream / LTX for
    # the card (a CUDA Kokoro OOMed mid-run while HiDream held 17 GB, 2026-09-06).
    kokoro_device: Literal["cpu", "cuda"] = "cpu"

    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    locale: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")


class SpeechRestorationSettings(StrictModel):
    """restore_speech: the voice chain between the synthesized/recorded take and the mix.

        detection -> cleanup -> band extension -> restoration -> de-esser -> EQ -> compression

    The FFmpeg tail (de-esser, EQ, light compression, and the resample to the delivery rate) needs
    nothing but FFmpeg, so it is on by default. The two model steps are opt-in exactly like the
    other backends in this repo: their weights live in ``models/speech_restoration/`` and their
    code in ``skills/audio/{clearervoice,resemble_enhance}``, each in its own uv environment.
    """

    enabled: bool = True
    # ClearerVoice MossFormer2_SE_48K: noise, hum and room off a dirty take.
    cleanup: Literal["off", "clearervoice"] = "off"
    # ClearerVoice MossFormer2_SR_48K: rebuilds the band a 16/24 kHz TTS never produced.
    band_extension: Literal["off", "clearervoice_sr"] = "off"
    # Resemble Enhance: the main restoration pass (denoiser + latent CFM + UnivNet at 44.1 kHz).
    enhancer: Literal["off", "resemble_enhance"] = "off"
    enhancer_mode: Literal["enhance", "denoise"] = "enhance"
    enhancer_nfe: int = Field(default=32, ge=1, le=128)
    enhancer_solver: Literal["midpoint", "rk4", "euler"] = "midpoint"
    enhancer_lambd: float = Field(default=0.5, ge=0, le=1)
    enhancer_tau: float = Field(default=0.5, ge=0, le=1)
    # detected: cleanup and band extension run only on the beats whose measurements ask for them.
    # always: run them on every beat, which is what an evaluation pass wants.
    gate: Literal["detected", "always"] = "detected"
    # CPU by default, like Kokoro: the card belongs to the image/video models during a run.
    # Resemble Enhance is ~19x realtime on this CPU, so a real run wants cuda between GPU stages.
    device: Literal["cpu", "cuda"] = "cpu"
    sample_rate_hz: Literal[44100, 48000] = 48000
    timeout_s: int = Field(default=3600, ge=30, le=86400)


class TranscriptionSettings(StrictModel):
    """transcribe_audio: reading the words off a recording nobody wrote a script for.

    ``faster_whisper`` measures word timings in its own uv environment (CPU int8, so it costs no
    VRAM while the image models hold the card). ``fixture`` takes a transcript the operator
    already has — a repo-relative or absolute text file, or the node's own ``transcript`` widget —
    and apportions it across the recording by word length, recording itself as ``estimated``
    rather than measured. That is the offline path the core suite runs on, and the honest answer
    for a recording whose script is known.

    The default is the measured one: a lane whose whole premise is "make a film out of what this
    person said" must not silently fall back to text nobody checked against the audio.
    """

    engine: Literal["faster_whisper", "fixture"] = "faster_whisper"
    faster_whisper_model: str = "base.en"
    faster_whisper_compute_type: str = "int8"
    timeout_s: int = Field(default=1800, ge=10, le=86400)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    # The rate the recording is normalised to before anything measures or cuts it. 24 kHz is what
    # voice_over normalises a take to, and the restoration chain resamples to its own delivery
    # rate afterwards, so matching voice_over is what keeps one recording indistinguishable from
    # a set of per-beat takes.
    sample_rate_hz: int = Field(default=24000, ge=8000, le=192000)
    # How many drawings a film gets when the node does not say. Six is a two-minute recording at
    # about twenty seconds a picture, which is the longest a still can hold before it reads as a
    # stalled video rather than an illustration.
    beats: int = Field(default=6, ge=1, le=60)


class VoiceOverSettings(StrictModel):
    """voice_over: human takes recorded outside the factory, force-aligned to the locked script.

    Takes live in ``takes_dir`` — relative to the run's project directory unless absolute, and an
    absolute path is usually what an operator wants, since the project directory is per run — as
    ``<beat_id>.wav`` — or
    ``<beat_id>.<speaker>.wav`` when two people share a scene. Nothing is ever generated here:
    a missing take is a typed failure, not a synthesized substitute.
    """

    takes_dir: str = "takes"
    # forced alignment (ADR-0004 precedence): faster-whisper is the installed fallback; whisperx
    # when its env exists; even_split is the offline stand-in used by the core test suite.
    aligner: Literal["even_split", "faster_whisper", "whisperx"] = "even_split"
    faster_whisper_model: str = "base.en"
    faster_whisper_compute_type: str = "int8"
    aligner_timeout_s: int = Field(default=600, ge=10, le=7200)
    # Take review (14.3): below this script similarity the take is sent back to be re-recorded.
    script_similarity_min: float = Field(default=0.85, ge=0.0, le=1.0)
    speaker_gain_db: dict[str, float] = Field(default_factory=dict)


class SoundDesignSettings(StrictModel):
    """sound_design: video-synced SFX under the narration (MMAudio large 44k v2, ADR-0004 roles)."""

    backend: Literal["mock", "mmaudio"] = "mock"
    # Lane-neutral on purpose. This used to read "footsteps on wet stone, distant sea wind, cloth
    # rustle" — the ambience of one coastal walk, applied as the default to every lane in the
    # catalogue, so a film about interest rates got footsteps and sea wind. MMAudio watches the
    # picture, so a prompt that names no specific place lets it follow what is actually on screen.
    # Per-shot prompts are the right answer and come after a live run has been measured; a
    # per-shot prompt guessed from here would be the same mistake at finer grain.
    prompt: str = "the natural ambience of whatever is shown, quiet and unobtrusive"
    negative_prompt: str = "music, speech, narration"
    # Place the curated library's sounds from a deterministic cue sheet (audio.cues): a bed under
    # the film, a soft mark on each cut, an accent where a card actually reveals something. Rules
    # over the compiled timeline, so it needs no model and no GPU and is the same on every run.
    library_cues: bool = True
    # The cue track's trim in the mix, on top of each cue's own gain. The library is already
    # normalised to a bed target, so this is placement and not a repair.
    cue_gain_db: float = Field(default=-6.0, ge=-60.0, le=0.0)
    # Conditioning (content_factory.audio.condition): measure the generated bed, repair only what
    # is broken, and normalise it to a known loudness. Without it the bed's place in the mix
    # depends on how loud the model happened to render, which is not reproducible across prompts.
    # It shares the detection and true-peak stages with the speech chain and NONE of its models —
    # those were measured to destroy non-speech material (see the module docstring).
    condition: bool = True
    # Matches assets/sfx/library.json's bed target, so the runtime bed and the curated library sit
    # at the same level. About 9 LU under the -14 LUFS programme: audible, never competing.
    bed_lufs: float = Field(default=-23.0, ge=-40.0, le=0.0)
    bed_true_peak_dbtp: float = Field(default=-1.0, ge=-12.0, le=0.0)
    # The operator's trim on a *conditioned* bed. It starts at 0 because the level now comes from
    # bed_lufs rather than from a blind attenuation of whatever the model produced.
    bed_trim_db: float = Field(default=0.0, ge=-60.0, le=0.0)
    # The attenuation applied when conditioning is off: the bed is at the model's own level, so it
    # needs a large blind cut to sit under speech.
    gain_db: float = Field(default=-22.0, ge=-60.0, le=0.0)
    seed: int = Field(default=7, ge=0)
    steps: int = Field(default=25, ge=1, le=200)
    # MMAudio generates in fixed-length windows; longer videos are generated per window and joined.
    window_s: float = Field(default=8.0, gt=0.0, le=30.0)
    timeout_s: int = Field(default=1800, ge=30, le=14400)


class LocalServicesSettings(StrictModel):
    """GPU servers the stages bring up themselves (services/local.py): the HiDream skill server
    for generate_anchor and ComfyUI for generate_video. On a 24 GB card the two never share the GPU
    (HiDream ~17 GB, LTX-2.5 GGUF ~20 GB), so starting one stops the other first."""

    auto_start: bool = True
    exclusive_gpu: bool = True
    # Stop the model servers when the last run finishes. A server keeps its weights loaded so the
    # next request does not pay the ~72 s load, which is right *while work is queued* and wrong
    # once the queue is empty: measured on 2026-09-10, an idle HiDream held **18,936 MiB** and the
    # card reported 3.2 GiB free, so the other tenant on this machine could not have used it.
    # Idle *power* is not the argument — 34.11 W with the model resident against 34.09 W without,
    # both at P8, indistinguishable. The 19 GB is.
    release_when_idle: bool = True
    startup_timeout_s: int = Field(default=1200, ge=30)
    poll_interval_s: float = Field(default=2.0, ge=0.2, le=30.0)
    hidream_skill_dir: str = "skills/image/hidream"
    # Which inference recipe the HiDream server uses: full is 50 steps at guidance 5, dev is 28
    # steps at guidance 0 with the distilled timestep schedule. These are recipes, not weights —
    # the two upstream repos ship *different* shards (1-7 differ; shard 8 happens to be identical,
    # which is why a single-file hash check is not enough), so this has to match what is on disk.
    # The weights here are HiDream-O1-Image-Dev (shard 1 sha256 575a1b54a028...), and this was set
    # to "full" — every image was pushed through nearly twice the steps it needed at a guidance the
    # model was distilled not to require. `hidream doctor` now checks the two against each other.
    hidream_model_type: Literal["full", "dev"] = "dev"
    # comfy-cli is only asked where the workspace is (`comfy which`); ComfyUI itself is launched
    # from that workspace's own .venv (comfy-cli's `launch` runs main.py under its tool Python,
    # which lacks ComfyUI's newer deps — verified 2026-09-06: `No module named comfy_aimdo`).
    comfy_bin: str = "comfy"
    comfy_workspace: str = ""  # empty = `comfy which`
    # LTX-2.5 needs the RAM cache off and headroom reserved (STATUS 2026-09-05), or it OOMs.
    comfy_extra_args: tuple[str, ...] = ("--cache-none", "--reserve-vram", "1.5")
    # ComfyUI's models/ directory; empty = <workspace>/models.
    comfy_models_dir: str = ""
    # Before ComfyUI starts, symlink every RequiredModel of the known packages from the weight
    # store into ComfyUI's model folders (idempotent; existing files are never replaced).
    link_models: bool = True
    weight_store: str = "/mnt/fast/models"


class RemoteHost(StrictModel):
    """Another machine in this tailnet that runs whole lanes of its own.

    Producing on a second box is the only way to use the stages that cannot be pooled over HTTP —
    the post chain, MMAudio, TTS and Blender all take absolute local paths (postchain/runner.py),
    so unlike HiDream they cannot be pointed at an endpoint. The cost of that is finished work on
    the wrong disk, which is what `content-factory remote harvest` collects.
    """

    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    """How the operator refers to the host, and the directory harvested work lands under."""
    ssh: str = Field(min_length=1, max_length=128)
    """An ssh target, not an address: `~/.ssh/config` already carries the host, and a tailnet IP
    written here is the same configuration in two places, renumbering under you. The connection is
    one-way by design - the control plane reaches the worker, never the reverse."""
    repo_root: str = "~/git/auto_content"
    runs_root: str = "output"
    """Where that host's runs live, relative to `repo_root` or absolute. Every project directory
    directly under it is a candidate."""
    services_dir: str = ".services"
    """That host's own `CF_SERVICES_DIR`, holding the active-run registry a harvest must not
    collect from underneath."""


class RemoteSettings(StrictModel):
    """The other producers, and bringing their finished work home.

    Empty `hosts` turns the whole feature off, including its doctor checks, and is byte for byte
    what this repo did before.

    In `.env`, **single-quote the JSON**. `.env` is sourced by bash, which eats the inner double
    quotes and hands pydantic `[{name:nova,...}]`; the error says
    `SettingsError: error parsing value for field "remote"` and does not mention quoting. Same
    trap as `CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS`:

        CF__REMOTE__HOSTS='[{"name":"nova","ssh":"nova@100.82.150.94"}]'
    """

    hosts: tuple[RemoteHost, ...] = ()
    harvest_root: str = "output/harvest"
    """Where collected deliverables land, relative to the repo. One directory per host per run."""
    publish_to_gallery: bool = True
    evidence: tuple[str, ...] = (
        "run.json",
        "qc/report.json",
        "sequence/chain.json",
        "sequence/lock.json",
        "reviews/frames/batch.json",
        "reviews/frames/verdict.json",
    )
    """Small files beyond the package that make a harvested run diagnosable rather than only
    watchable - a few hundred KB against a deliverable's tens of MB. Fetched when present; a
    missing one is never an error, because which of them exists depends on the lane."""
    bwlimit_kbps: int = Field(default=0, ge=0)
    """0 is no limit. Worth setting if a harvest loop runs while this machine is rendering: both
    write to the same NVMe."""
    ssh_timeout_s: int = Field(default=20, ge=5, le=300)
    transfer_timeout_s: int = Field(default=1800, ge=30)

    def host_named(self, name: str) -> RemoteHost | None:
        return next((h for h in self.hosts if h.name == name), None)

    def enabled(self) -> bool:
        return bool(self.hosts)


class WebPushSettings(StrictModel):
    enabled: bool = True
    vapid_keys_env: str = "VAPID_KEYS"


class NtfySettings(StrictModel):
    enabled: bool = False
    endpoint: str | None = None
    topic: str | None = None


class EmailNotificationSettings(StrictModel):
    enabled: bool = False


class NotificationSettings(StrictModel):
    web_push: WebPushSettings = WebPushSettings()
    ntfy: NtfySettings = NtfySettings()
    email: EmailNotificationSettings = EmailNotificationSettings()


class PrivacySettings(StrictModel):
    artifact_retention_days: int = Field(default=0, ge=0)  # 0 = keep
    audit_retention_days: int = Field(default=365, ge=1)


class SecuritySettings(StrictModel):
    egress_allowlist_required: bool = True
    signed_artifact_urls_only: bool = True
    artifact_url_ttl_seconds: int = Field(default=600, ge=30)


class Settings(BaseSettings):
    """Root configuration. Loaded once per process via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="CF__",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
        yaml_file=os.environ.get(CONFIG_FILE_ENV, DEFAULT_CONFIG_FILE),
        yaml_file_encoding="utf-8",
    )

    environment: Literal["development", "test", "production"] = "development"
    bind: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    public_base_url: str = "http://localhost:3000"

    auth: AuthSettings = AuthSettings()
    tailscale: TailscaleSettings = TailscaleSettings()
    database: DatabaseSettings = DatabaseSettings()
    object_store: ObjectStoreSettings = ObjectStoreSettings()
    token_vault: TokenVaultSettings = TokenVaultSettings()
    temporal: TemporalSettings = TemporalSettings()
    theme: ThemeSettings = ThemeSettings()
    budgets: BudgetSettings = BudgetSettings()
    execution: ExecutionSettings = ExecutionSettings()
    providers: ProvidersSettings = ProvidersSettings()
    comfyui: ComfyUISettings = ComfyUISettings()
    video: VideoSettings = VideoSettings()
    postchain: PostChainSettings = PostChainSettings()
    media_library: MediaLibrarySettings = MediaLibrarySettings()
    gallery: GallerySettings = GallerySettings()
    animation: AnimationSettings = AnimationSettings()
    search: SearchSettings = SearchSettings()
    research: ResearchSettings = ResearchSettings()
    radar: RadarSettings = RadarSettings()
    preflight: PreflightSettings = PreflightSettings()
    approvals: ApprovalsSettings = ApprovalsSettings()
    originality: OriginalitySettings = OriginalitySettings()
    deliverables: DeliverablesSettings = DeliverablesSettings()
    distribution: DistributionSettings = DistributionSettings()
    attribution: AttributionSettings = AttributionSettings()
    personas: PersonaSettings = PersonaSettings()
    portal: PortalSettings = PortalSettings()
    engagement: EngagementSettings = EngagementSettings()
    human_tasks: HumanTaskSettings = HumanTaskSettings()
    style_exploration: StyleExplorationSettings = StyleExplorationSettings()
    image_sequences: ImageSequenceSettings = ImageSequenceSettings()
    reference: ReferenceSettings = ReferenceSettings()
    shots: ShotSettings = ShotSettings()
    controls: ControlSettings = ControlSettings()
    routing: RoutingSettings = RoutingSettings()
    narration: NarrationSettings = NarrationSettings()
    compose: ComposeSettings = ComposeSettings()
    transcription: TranscriptionSettings = TranscriptionSettings()
    voice_over: VoiceOverSettings = VoiceOverSettings()
    speech_restoration: SpeechRestorationSettings = SpeechRestorationSettings()
    sound_design: SoundDesignSettings = SoundDesignSettings()
    local_services: LocalServicesSettings = LocalServicesSettings()
    remote: RemoteSettings = RemoteSettings()
    notifications: NotificationSettings = NotificationSettings()
    privacy: PrivacySettings = PrivacySettings()
    security: SecuritySettings = SecuritySettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # init > env > yaml > defaults. dotenv is intentionally excluded: secrets come from the
        # process environment or secret files, never from a committed file.
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls))

    @model_validator(mode="after")
    def _startup_invariants(self) -> Settings:
        """Refuse configurations that would violate immutable safety policy."""
        if self.tailscale.mode == TailscaleMode.funnel:
            # Funnel exposes the app to the public internet: password auth + MFA are mandatory.
            if self.auth.mode != AuthMode.local:
                msg = "tailscale.mode=funnel requires auth.mode=local (password + MFA)"
                raise ValueError(msg)
            if not (self.auth.totp_enabled or self.auth.passkeys_enabled):
                msg = "tailscale.mode=funnel requires MFA (TOTP or passkeys) to be enabled"
                raise ValueError(msg)
        if self.bind not in {"127.0.0.1", "localhost", "::1"} and self.tailscale.mode == "off":
            # Binding beyond loopback is allowed only for tailnet interfaces (100.64.0.0/10) —
            # never for 0.0.0.0. Tailnet access should use `tailscale serve` against loopback.
            if self.bind in {"0.0.0.0", "::"}:  # noqa: S104 - detection, not a bind
                msg = "bind=0.0.0.0 is refused; use loopback + `tailscale serve` (section 3.3)"
                raise ValueError(msg)
        return self


def load_settings(config_file: str | Path | None = None, **overrides: object) -> Settings:
    """Load settings from an explicit YAML file (or the default) plus environment overrides."""
    if config_file is not None:
        return Settings.model_validate(
            {**_yaml_dict(Path(config_file)), **overrides},
            context=None,
        )
    return Settings(**overrides)  # type: ignore[arg-type]


def _yaml_dict(path: Path) -> dict[str, object]:
    import yaml  # local import: only needed when a file is given

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        msg = f"Config file {path} must contain a mapping at the top level"
        raise ValueError(msg)
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
