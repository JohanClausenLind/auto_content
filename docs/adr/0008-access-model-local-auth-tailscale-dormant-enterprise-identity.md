# ADR 0008 — Access model: local auth by default, Tailscale for remote access, dormant enterprise identity

- Status: accepted (2026-08-27)
- Research: docs/research/infra-and-frontend.md (Tailscale, Web Push)

## Context
One operator, possibly a few collaborators later, self-hosting on a home server that is already a
Tailscale node (`vegaserv.tail07205e.ts.net`; an existing `tailscale serve` on 443 proxies another
app). No identity product should be built, but the scale-later path must stay open.

## Decision
- `auth.mode: local` (default): one operator account + optional collaborators. **Argon2id**
  passwords (argon2-cffi 25.1.0), DB-backed opaque session tokens in HttpOnly/SameSite cookies,
  **passkeys/WebAuthn** via py_webauthn 3.0.0 (registration + authentication ceremonies proven in
  phase 0 against a software authenticator, including stale-challenge and wrong-origin rejection),
  **RFC 6238 TOTP** via pyotp 2.10.0 with time-step replay protection, single-use recovery codes
  (hashed), session/device list and revocation, and step-up re-auth for sensitive actions
  (Autopilot, DistributionProfile authorization, connecting accounts, kill switch).
- Roles: Owner, Editor, Reviewer, Viewer; deny-by-default RBAC on destructive, connection,
  Autopilot, and publishing actions.
- **Tailscale**: the app binds loopback; tailnet HTTPS comes from `tailscale serve` against the
  loopback port. Because this machine already serves another app on 443, Content Factory is
  served on a **separate HTTPS port (8443)**: `tailscale serve --bg --https=8443 <port>`.
  Documented alternative: `tailscale cert` + Caddy. Optional Tailscale identity verifies headers
  by shelling to `tailscale whois --json` on each new session; headers are never trusted alone.
  **Funnel refuses to start unless password auth + MFA are enabled** (enforced at settings
  validation). Model-endpoint hostnames that fail DNS resolve through `tailscale status --json`
  peers. Postgres, Temporal, ComfyUI, Ollama, object store, and worker ports are never exposed
  beyond loopback/tailnet.
- **Dormant enterprise identity**: `auth.mode: oidc` implemented behind Authlib (BSD/commercial
  dual — BSD applies) with fixture tests; `auth.mode: saml` and SCIM designed (schema + fixtures +
  contract tests, no live dependency). OIDC, SAML, and SCIM are modelled separately. Local mode
  never requires them.
- Web Push uses VAPID (RFC 8292) via pywebpush 2.4.0 (MPL-2.0); iOS 16.4+ delivers push only to
  Home Screen web apps with `display: standalone`. Push notifies and deep-links; it never
  approves.

## Consequences
- No shared external-platform passwords or credential proxying anywhere.
- Enabling funnel is a deliberate, loud configuration act.
