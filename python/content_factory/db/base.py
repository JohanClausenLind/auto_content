"""SQLAlchemy declarative base, id generation, and shared mixins.

Every domain row carries ``workspace_id`` (mixin :class:`WorkspaceScoped`). IDs are opaque
strings ``<prefix>_<22 base62 chars>`` matching ``schemas.base.OpaqueId``.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

_ALPHABET = string.ascii_letters + string.digits


def new_id(prefix: str) -> str:
    if not (1 <= len(prefix) <= 8) or not prefix.isalnum() or not prefix[0].isalpha():
        msg = f"invalid id prefix {prefix!r}"
        raise ValueError(msg)
    body = "".join(secrets.choice(_ALPHABET) for _ in range(22))
    return f"{prefix.lower()}_{body}"


def utcnow() -> datetime:
    return datetime.now(UTC)


NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class WorkspaceScoped:
    """Mixin: rows belong to exactly one workspace; queries must filter on it."""

    @declared_attr
    def workspace_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(
            String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
        )
