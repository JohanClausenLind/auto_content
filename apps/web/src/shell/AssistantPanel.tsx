import { Dialog } from "@content-factory/web-ui";

/** The MCP tools the control plane exposes; the same surface external agents use. */
const MCP_TOOLS: { name: string; description: string }[] = [
  { name: "list_capabilities", description: "What this factory can make, and which tools exist." },
  { name: "list_runs", description: "Recent pipeline runs in a workspace." },
  { name: "get_status", description: "One run's state and step-by-step progress." },
  { name: "list_action_items", description: "Open items waiting on a human decision." },
  { name: "create_campaign", description: "Start a fixture campaign run." },
  { name: "approve_preflight", description: "Record an approve or reject decision on a waiting run." },
  { name: "submit_revision_feedback", description: "Turn plain feedback into a typed fix plan." },
  { name: "apply_approved_edit_batch", description: "Apply an approved fix plan and rebuild." },
];

export function AssistantPanel({ isOpen, onOpenChange }: { isOpen: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog
      title="Assistant"
      description="The assistant connects through the same MCP surface external agents use. A local model endpoint isn't configured in this build, so there's no chat here — only the honest list of what an agent could do."
      size="sm"
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      modalProps={{ className: "cf-overlay cf-overlay--right" }}
    >
      <div className="cf-assistant">
        <h3 className="cf-assistant__heading">Available MCP tools</h3>
        <ul className="cf-assistant__tools">
          {MCP_TOOLS.map((tool) => (
            <li key={tool.name} className="cf-assistant__tool">
              <code className="cf-assistant__name">{tool.name}</code>
              <span className="cf-assistant__desc">{tool.description}</span>
            </li>
          ))}
        </ul>
        <p className="cf-assistant__cli">
          To connect an agent, run <code>content-factory mcp</code> on the server and point the agent at it.
        </p>
      </div>
    </Dialog>
  );
}
