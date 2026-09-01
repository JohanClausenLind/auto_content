"""DistributionBackend protocol (22) and typed publish machinery.

Publishing is a side effect, never a cacheable result: every attempt runs through a PublishIntent
whose key derives from campaign/deliverable/package/backend/account/destination revisions. Before
any retry the service checks the local intent, then reconciles against the provider by content,
and creates a new post only when absence is proven. Ambiguity is a blocking reconciliation state,
never a blind retry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from content_factory.schemas.base import sha256_hex


class PublishState(StrEnum):
    pending = "pending"
    published = "published"
    ambiguous = "ambiguous"  # a submit may or may not have landed; reconcile before anything else
    blocked = "blocked"
    failed = "failed"


class PublishBlockedError(Exception):
    pass


class TransientPublishError(Exception):
    """Retryable: connection loss, 5xx, rate limits (after waiting)."""


class AmbiguousPublishError(Exception):
    """The request may have reached the platform (timeout after send). Never blindly retry."""


@dataclass(frozen=True)
class MediaAttachment:
    data: bytes
    mime: str
    alt_text: str


@dataclass(frozen=True)
class PostPackage:
    """One approved DestinationPackage ready for one destination."""

    text: str
    media: tuple[MediaAttachment, ...] = ()
    visibility: str = "public"
    language: str = "en"
    idempotency_token: str = ""  # stable per intent; platforms with native support use it


@dataclass(frozen=True)
class PublishReceipt:
    remote_id: str
    url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    platform: str
    max_text_chars: int
    max_images: int
    supports_alt_text: bool
    supports_idempotency_key: bool
    supported_visibilities: tuple[str, ...]
    max_image_bytes: int


class DistributionBackend(ABC):
    platform: str

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def validate(self, package: PostPackage) -> list[str]:
        """Local validation against current capabilities; plain-language problems."""

    @abstractmethod
    def publish(self, package: PostPackage) -> PublishReceipt:
        """Submit exactly one post. Raises Transient/Ambiguous errors as appropriate."""

    @abstractmethod
    def find_existing(self, package: PostPackage) -> PublishReceipt | None:
        """Reconciliation: find a post that IS this package (by idempotency token or content)."""


def intent_key(
    *,
    workspace_id: str,
    campaign_id: str,
    deliverable_id: str,
    package_revision: str,
    platform: str,
    account_id: str,
) -> str:
    raw = "|".join(
        [workspace_id, campaign_id, deliverable_id, package_revision, platform, account_id]
    )
    return sha256_hex(raw.encode())
