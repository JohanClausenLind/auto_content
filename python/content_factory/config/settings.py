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


class ComfyUISettings(StrictModel):
    enabled: bool = True
    managed_by_comfy_cli: bool = True
    workspace: str = "./.comfy"
    endpoint: str = "http://127.0.0.1:8188"
    production_workflows_only: bool = True
    allow_arbitrary_custom_nodes: bool = False
    workflow_lab: WorkflowLabSettings = WorkflowLabSettings()


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


class DriftThresholds(StrictModel):
    locked_region_similarity: float = Field(default=0.92, ge=0, le=1)
    style_delta_max: float = Field(default=0.15, ge=0, le=1)


class ImageSequenceSettings(StrictModel):
    enabled: bool = True
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
    engagement: EngagementSettings = EngagementSettings()
    human_tasks: HumanTaskSettings = HumanTaskSettings()
    style_exploration: StyleExplorationSettings = StyleExplorationSettings()
    image_sequences: ImageSequenceSettings = ImageSequenceSettings()
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
