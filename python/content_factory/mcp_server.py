"""The MCP server (ADR 0007): the ONLY external-agent surface.

Agents connect with a scoped token (CF_MCP_TOKEN); they never receive OAuth tokens, never widen
publish scope, and every write lands in the same audit/approval machinery as the UI. Run it with
`content-factory mcp` (stdio) — see integrations/mcp/README.md.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

INSTRUCTIONS = """Content Factory operations. Reads are free; every write is typed, auditable,
and gated: approvals bind to exact preflight revisions, revision feedback compiles to typed fix
plans, and nothing here can publish, widen visibility, or spend beyond configured budgets."""


def _authorized() -> bool:
    expected = os.environ.get("CF_MCP_TOKEN", "")
    presented = os.environ.get("CF_MCP_CLIENT_TOKEN", "")
    return bool(expected) and hmac.compare_digest(expected, presented)


def create_server() -> MCPServer:
    mcp = MCPServer("content-factory", instructions=INSTRUCTIONS)

    def _guard() -> None:
        if not _authorized():
            msg = "unauthorized: set CF_MCP_TOKEN (server) and CF_MCP_CLIENT_TOKEN (client) to the same scoped token"  # noqa: E501
            raise PermissionError(msg)

    @mcp.tool()
    def list_capabilities() -> dict[str, Any]:
        """What this deployment can do right now (honest, per configuration)."""
        from content_factory.config import get_settings

        s = get_settings()
        return {
            "deliverable_types": list(s.deliverables.allowed_types),
            "distribution_enabled": s.distribution.enabled,
            "kill_switch": s.distribution.kill_switch,
            "engagement_enabled": s.engagement.enabled,
            "execution_default_policy": s.execution.default_policy.value,
            "notes": "Distribution and engagement ship disabled; enabling them requires operator authorization in the app.",  # noqa: E501
        }

    @mcp.tool()
    async def list_runs(workspace_id: str = "ws_demo00000001") -> list[dict[str, Any]]:
        """List production runs in a workspace."""
        _guard()
        from content_factory.db.session import session_scope
        from content_factory.services.runs import list_runs as _list

        async with session_scope() as db:
            return await _list(db, workspace_id)

    @mcp.tool()
    async def get_status(run_id: str, workspace_id: str = "ws_demo00000001") -> dict[str, Any]:
        """Full run status: state, preflight revision, per-node progress."""
        _guard()
        from content_factory.db.session import session_scope
        from content_factory.services.runs import run_view

        async with session_scope() as db:
            view = await run_view(db, workspace_id, run_id)
        if view is None:
            msg = f"run {run_id} not found in {workspace_id}"
            raise ValueError(msg)
        return view

    @mcp.tool()
    async def list_action_items(
        workspace_id: str = "ws_demo00000001", status: str = "open"
    ) -> list[dict[str, Any]]:
        """Open items that need the operator (approvals, failures)."""
        _guard()
        from content_factory.db.session import session_scope
        from content_factory.services.runs import list_action_items as _list

        async with session_scope() as db:
            return await _list(db, workspace_id, status=status)

    @mcp.tool()
    async def create_campaign(quality: str = "demo") -> dict[str, str]:
        """Start the fixture demo campaign as a durable production run (package-only; no publishing)."""  # noqa: E501
        _guard()
        if quality not in {"smoke", "demo"}:
            msg = "quality must be smoke or demo"
            raise ValueError(msg)
        from content_factory.schemas.fixtures import sample_campaign
        from content_factory.services.runs import start_run

        run_id = await start_run(sample_campaign(), quality=quality)
        return {
            "run_id": run_id,
            "next": "the run parks at WAITING_FOR_APPROVAL; approve with approve_preflight",
        }

    @mcp.tool()
    async def approve_preflight(
        run_id: str, revision_hash: str, workspace_id: str = "ws_demo00000001"
    ) -> dict[str, str]:
        """Approve a waiting run. The hash must match the exact preflight revision (stale hashes are refused by the workflow)."""  # noqa: E501
        _guard()
        from content_factory.services.runs import approve_run

        await approve_run(run_id, actor="mcp-agent", revision_hash=revision_hash)
        return {
            "run_id": run_id,
            "submitted": "approval",
            "note": "a stale revision hash is recorded and ignored by the workflow",
        }

    @mcp.tool()
    def submit_revision_feedback(project_id: str, feedback: str) -> dict[str, Any]:
        """Revision Box: map plain-language feedback to a typed outcome (fix plan, clarifying
        question, refusal, or gate). Nothing is applied."""
        _guard()
        from content_factory.editor.critique import map_feedback
        from content_factory.editor.project_context import load_context
        from content_factory.services.runs import projects_root

        ctx = load_context(projects_root() / project_id)
        return {"outcome": map_feedback(feedback, ctx).model_dump(mode="json")}

    @mcp.tool()
    async def apply_approved_edit_batch(project_id: str, feedback: str) -> dict[str, Any]:
        """Apply the fix plan for this feedback and start a targeted rebuild of the same project."""
        _guard()
        import json as _json

        from content_factory.editor.apply import apply_fix_plan
        from content_factory.editor.critique import map_feedback
        from content_factory.editor.project_context import load_context
        from content_factory.schemas.content import ContentCampaign
        from content_factory.schemas.editing import FixPlan
        from content_factory.services.runs import projects_root, start_run

        project_dir = projects_root() / project_id
        outcome = map_feedback(feedback, load_context(project_dir))
        if not isinstance(outcome, FixPlan):
            return {"applied": False, "outcome": outcome.model_dump(mode="json")}
        applied = apply_fix_plan(project_dir, outcome)
        campaign_path = project_dir / "campaign.json"
        if not campaign_path.exists():
            msg = "project has no stored campaign; run it once through the pipeline first"
            raise ValueError(msg)
        campaign = ContentCampaign.model_validate_json(campaign_path.read_text())
        quality = _json.loads((project_dir / "manifest.json").read_text()).get("quality", "demo")
        run_id = await start_run(campaign, quality=quality, project_id=project_id)
        return {
            "applied": True,
            "revision": applied.revision,
            "run_id": run_id,
            "affected_unit_ids": list(applied.affected_unit_ids),
        }

    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
