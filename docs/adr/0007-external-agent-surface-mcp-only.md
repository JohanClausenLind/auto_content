# ADR 0007 — The only external-agent surface is a narrow MCP server

- Status: accepted (2026-08-27)

## Context
The operator uses several assistants (Claude, OpenClaw, Hermes Agent, phone shortcuts) and runs
sibling projects (e.g. Odysseus) with their own agent loops, tool registries, and plugin systems.
Each has useful patterns; none should become a dependency or a code import.

## Decision
- Content Factory exposes exactly one agent surface: an **MCP server** (`integrations/mcp`) with
  typed tools mirroring the REST API (`create_campaign`, `run_preflight`, `approve_preflight`,
  `submit_revision_feedback`, `apply_approved_edit_batch`, `create_distribution_campaign`,
  `list_human_tasks`, `submit_human_task_asset`, `get_engagement_inbox`, `create_image_sequence`,
  …). Callers hold scoped API principals with hashed credentials, expiry, rate limits, and audit
  events; they select only pre-authorized accounts/profiles, never receive OAuth tokens, and cannot
  bypass approval, disclosure, consent, or budget gates. Public visibility requires an explicit
  policy value.
- The in-app global assistant is an MCP client of the same server, so there is one permission
  model and one audit trail for humans, the in-app assistant, and external agents.
- **No OpenClaw plugin, Hermes skill, or integration directory.** OpenClaw and Hermes connect as
  MCP clients with scoped tokens; the approval PWA covers mobile control.
- **No vendoring** of Odysseus, OpenClaw, Hermes, n8n, Activepieces, or Trigger.dev code. The
  useful parts are patterns we adopt directly: Odysseus's five-token theme presets with derived
  colours and localStorage-first/server-backup persistence; its "assistant = one pinned session"
  simplicity; n8n/Temporal UI/Trigger.dev run-timeline UX. Copying code would import their
  abstractions (agent loops, tool schemas, credential handling) without their ecosystems, and
  n8n/Activepieces are fair-code/paid-embed.

## Security rationale
A single narrow surface means one place to enforce scopes, one audit log, no long-lived provider
tokens outside the vault, and no plugin code executing inside our process. Agents propose typed
operations; validators and deterministic services apply them.

## Consequences
- Any new agent integration is "issue an MCP principal", not "write a plugin".
- MCP tool schemas are generated from the same Pydantic contracts as the REST API.
