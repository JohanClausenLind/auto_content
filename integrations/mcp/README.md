# MCP server — the only external-agent surface (ADR 0007)

Run (stdio):
```bash
CF_MCP_TOKEN=<scoped-token> CF_MCP_CLIENT_TOKEN=<same> uv run content-factory mcp
```
Register in an agent (e.g. Claude Code): command `uv`, args `run content-factory mcp`, env
`CF_MCP_TOKEN`/`CF_MCP_CLIENT_TOKEN` plus `DATABASE_URL`. Agents get typed tools
(`list_capabilities`, `list_runs`, `get_status`, `list_action_items`, `create_campaign`,
`approve_preflight`, `submit_revision_feedback`, `apply_approved_edit_batch`) and never OAuth
tokens, publishing scope, or budget overrides. Reads work without gates; writes go through the
same approval/audit machinery as the web app.
