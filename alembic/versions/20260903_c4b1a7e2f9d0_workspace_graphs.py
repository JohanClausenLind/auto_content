"""workspace graphs (node-graph editor documents)

Revision ID: c4b1a7e2f9d0
Revises: 8c5a9db8e9e5
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4b1a7e2f9d0"
down_revision: str | None = "8c5a9db8e9e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_graphs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("doc", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_graphs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_workspace_graphs_workspace_id"), "workspace_graphs", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_workspace_graphs_ws_updated", "workspace_graphs", ["workspace_id", "updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_graphs_ws_updated", table_name="workspace_graphs")
    op.drop_index(op.f("ix_workspace_graphs_workspace_id"), table_name="workspace_graphs")
    op.drop_table("workspace_graphs")
