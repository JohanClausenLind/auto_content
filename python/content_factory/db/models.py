"""Phase-1 identity, workspace, preference, and audit tables."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base, TimestampMixin, WorkspaceScoped


class Role(StrEnum):
    owner = "owner"
    editor = "editor"
    reviewer = "reviewer"
    viewer = "viewer"


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(back_populates="workspace")


class Account(TimestampMixin, Base):
    """Operator or collaborator. ``is_owner`` grants the Owner role everywhere."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # envelope-encrypted
    totp_last_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(back_populates="account")
    passkeys: Mapped[list[Passkey]] = relationship(back_populates="account")


class WorkspaceMembership(TimestampMixin, Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "account_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    account: Mapped[Account] = relationship(back_populates="memberships")


class Session(Base):
    """Opaque bearer stored hashed; the cookie carries the raw token."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    current_workspace_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    step_up_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)


class Passkey(Base):
    __tablename__ = "passkeys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="passkey")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="passkeys")


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthChallenge(Base):
    """Single-use WebAuthn challenges (registration or authentication), server-side only."""

    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # register | authenticate
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountPreference(TimestampMixin, Base):
    """Per-account key/value preferences (theme, UI state). Not workspace data."""

    __tablename__ = "account_preferences"
    __table_args__ = (UniqueConstraint("account_id", "key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class AuditEvent(Base):
    """Append-only. Never updated or deleted by application code."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_account_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class BrandKit(WorkspaceScoped, TimestampMixin, Base):
    """Minimal brand kit row (phase 1: name + token overrides). Grows in phase 2."""

    __tablename__ = "brand_kits"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tokens: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RunState(StrEnum):
    created = "CREATED"
    preflighting = "PREFLIGHTING"
    waiting_for_approval = "WAITING_FOR_APPROVAL"
    approved = "APPROVED"
    producing = "PRODUCING"
    complete = "COMPLETE"
    blocked = "BLOCKED"
    failed = "FAILED"
    cancelled = "CANCELLED"


class NodeState(StrEnum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


class ProductionRun(WorkspaceScoped, TimestampMixin, Base):
    __tablename__ = "production_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # temporal workflow id
    campaign_id: Mapped[str] = mapped_column(String(40), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[RunState] = mapped_column(
        Enum(RunState, name="run_state"), nullable=False, default=RunState.created
    )
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="demo")
    preflight_revision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class RunNode(WorkspaceScoped, TimestampMixin, Base):
    __tablename__ = "run_nodes"
    __table_args__ = (UniqueConstraint("run_id", "node_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("production_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    stage: Mapped[str] = mapped_column(String(60), nullable=False)
    deliverable_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state: Mapped[NodeState] = mapped_column(
        Enum(NodeState, name="node_state"), nullable=False, default=NodeState.queued
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActionItemStatus(StrEnum):
    open = "open"
    resolved = "resolved"


class ActionItem(WorkspaceScoped, TimestampMixin, Base):
    __tablename__ = "action_items"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(
        String(60), nullable=False
    )  # approval_waiting, run_failed, ...
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ActionItemStatus] = mapped_column(
        Enum(ActionItemStatus, name="action_item_status"),
        nullable=False,
        default=ActionItemStatus.open,
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deep_link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PushSubscription(TimestampMixin, Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "endpoint",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(1000), nullable=False)
    keys: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # p256dh + auth
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ConnectedAccount(WorkspaceScoped, TimestampMixin, Base):
    """A connected destination account. Token plaintext lives ONLY in the vault columns
    (sealed); models, logs, and the browser never see it."""

    __tablename__ = "connected_accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "platform", "handle"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    handle: Mapped[str] = mapped_column(String(200), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    token_key_id: Mapped[str] = mapped_column(String(24), nullable=False)
    token_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_key_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    refresh_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health: Mapped[str] = mapped_column(String(20), nullable=False, default="CONNECTED")


class OAuthState(WorkspaceScoped, Base):
    """Single-use OAuth intent: state + PKCE verifier, bound to workspace and redirect URI."""

    __tablename__ = "oauth_states"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DistributionProfile(WorkspaceScoped, TimestampMixin, Base):
    """Immutable authorized revisions: any change creates a new revision needing re-authorization."""  # noqa: E501

    __tablename__ = "distribution_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "revision"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )  # accounts, visibilities, cadence, caps
    authorized_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DistributionState(Base):
    """Per-workspace distribution switches (the kill switch is honoured by every publish path)."""

    __tablename__ = "distribution_state"

    workspace_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonaRow(WorkspaceScoped, TimestampMixin, Base):
    """Persisted persona: `document` holds the full validated Persona contract at `revision`.
    Revision history lives in the audit log (persona.apply records each diff)."""

    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # identity.display_name copy
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrandNodeRow(WorkspaceScoped, TimestampMixin, Base):
    """One node of the brand hierarchy; lock semantics are enforced by brands.hierarchy on write."""

    __tablename__ = "brand_nodes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("brand_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tokens: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    locked_tokens: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    policies: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    locked_policies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class PortalLink(WorkspaceScoped, TimestampMixin, Base):
    """Issued portal token (stored only as a hash, for listing and revocation)."""

    __tablename__ = "portal_links"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PortalBriefStatus(StrEnum):
    new = "new"
    accepted = "accepted"
    declined = "declined"


class PortalBrief(WorkspaceScoped, TimestampMixin, Base):
    """A brief submitted through the portal. Never creates a run by itself."""

    __tablename__ = "portal_briefs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    link_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("portal_links.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[PortalBriefStatus] = mapped_column(
        Enum(PortalBriefStatus, name="portal_brief_status"),
        nullable=False,
        default=PortalBriefStatus.new,
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
