# Scale later: what commercialization would add (none of it is built)

The architecture keeps the invariants that make this possible — stateless API, Postgres as
transactional truth, `workspace_id` on every domain row, ArtifactStore abstraction, durable
Temporal workflows, resource-class worker queues, typed ExecutionPolicies and cost ledger. The
codebase deliberately contains **none** of the following:

1. Billing: plans, subscriptions, entitlements, credits-as-money, invoices, taxes, dunning,
   merchant-of-record integration, usage-based pricing engines.
2. Commercial surfaces: pricing/marketing site, trial flows, closed-beta management,
   launch-readiness tracking, unit-economics dashboards.
3. Customer support: support consoles, SupportAccessGrant (impersonation), ticketing.
4. Multi-tenant hardening beyond one tenant: cross-tenant originality comparison, per-tenant
   rate limits/quotas, tenant-level encryption keys (customer-managed keys), noisy-neighbour
   isolation on shared workers, per-tenant egress accounting.
5. Enterprise identity in production: live OIDC/SAML/SCIM against real IdPs (the module is
   dormant with fixtures only), enforced MFA policies per tenant, audit-log streaming SLAs.
6. Compliance programme artefacts: trust centre, DPA/SCC templates, vendor-risk exports, SOC 2
   evidence collection, data-residency routing.
7. Marketplace: public skill/workflow marketplace, third-party skill sandboxing on shared
   workers, revenue sharing.
8. Operations at fleet scale: active-active multi-region, container render fleets, autoscaling
   GPU pools, per-tenant Temporal namespaces, SLO alerting integrations.
9. Platform partnerships: X/Meta/TikTok/LinkedIn partner-tier API access and app-review
   programmes for third-party posting on behalf of customers.

What exists instead, and why it is enough for one operator: budgets + cost ledger
(estimate → reserve → settle against operator caps), local auth with passkeys/TOTP, Tailscale
access, a single MCP agent surface, and one workspace/brand hierarchy.

## Explainer lane: deferred until episodes are published

Built only when a published episode shows the need; none of these exists as a stub.

| Deferred | What would trigger it |
|---|---|
| Manim (glyph-level equation morphing) | A storyboard where the explanation is the transformation of one equation into another, which KaTeX group reveals and highlights cannot show. It would enter as a cached offline asset behind the common asset interface, never as a frame renderer. |
| Blender | A scene that needs lit, physically shaped 3D objects beyond what Three.js inside Remotion can draw. |
| MapLibre / deck.gl | A map that must show tiled basemap detail or tens of thousands of points; until then D3-geo projects into the shared SVG renderer with no tile licensing or attribution plumbing. |
| Motion Canvas | A template Remotion cannot render as a pure function of the frame. |
| Analytics import | Published episodes with retention and click-through data worth feeding back into topic ranking and pacing. |
| Sponsor tooling | A signed sponsor; `docs/SPONSORS.md` already fixes the disclosure and isolation rules the script and spec enforce. |
| Multi-episode series structure | A second episode that depends on an earlier one's setup, so continuity of entities and terms has to span episodes. |
