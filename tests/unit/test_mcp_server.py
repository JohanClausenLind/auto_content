"""MCP surface: tool listing, token gating, and the read/critique tools in-process."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import TextContent

from content_factory.mcp_server import create_server


@asynccontextmanager
async def connected_client():
    server = create_server()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            lowlevel = server._lowlevel_server
            tg.start_soon(
                lambda: lowlevel.run(
                    server_read,
                    server_write,
                    lowlevel.create_initialization_options(),
                    raise_exceptions=False,
                )
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "prj_mcp0000000001"
    (root / "deliverables" / "dlv_carousel0001").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": "prj_mcp0000000001",
                "workspace_id": "ws_demo00000001",
                "campaign_id": "cmp_x",
                "quality": "demo",
            }
        )
    )
    (root / "deliverables" / "dlv_carousel0001" / "copy.json").write_text(
        json.dumps(
            {
                "cards": [
                    {"card_id": "card_000000000001", "text": "one"},
                    {"card_id": "card_000000000002", "text": "two"},
                ]
            }
        )
    )
    monkeypatch.setenv("CF_PROJECTS_DIR", str(tmp_path))
    return "prj_mcp0000000001"


def _text(result) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def test_tools_listed_and_capabilities_are_honest() -> None:
    async with connected_client() as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {
            "list_capabilities",
            "list_runs",
            "get_status",
            "create_campaign",
            "approve_preflight",
            "submit_revision_feedback",
            "apply_approved_edit_batch",
            "list_graphs",
            "run_graph",
            "get_run_outputs",
        } <= names
        result = await client.call_tool("list_capabilities", {})
        caps = json.loads(_text(result))
        assert caps["distribution_enabled"] is False and caps["kill_switch"] is True


async def test_writes_require_the_scoped_token(
    project: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CF_MCP_TOKEN", raising=False)
    async with connected_client() as client:
        result = await client.call_tool(
            "submit_revision_feedback", {"project_id": project, "feedback": "remove the citation"}
        )
        assert result.is_error  # the SDK masks the PermissionError detail; the call is refused
    monkeypatch.setenv("CF_MCP_TOKEN", "secret-token")
    monkeypatch.setenv("CF_MCP_CLIENT_TOKEN", "secret-token")
    async with connected_client() as client:
        result = await client.call_tool(
            "submit_revision_feedback",
            {"project_id": project, "feedback": "remove the source citation"},
        )
        assert not result.is_error
        outcome = json.loads(_text(result))["outcome"]
        assert outcome["kind"] == "refusal" and outcome["policy"] == "citations_required"
        good = await client.call_tool(
            "submit_revision_feedback",
            {"project_id": project, "feedback": 'card 2 should say "Better siting"'},
        )
        fix = json.loads(_text(good))["outcome"]
        assert fix["kind"] == "fix_plan" and fix["impact"]["affected_unit_ids"] == [
            "card_000000000002"
        ]
