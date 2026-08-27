# ADR 0009 — Personas and audience engagement: disclosure stance, immutable guardrails, reply-API tiers

- Status: accepted (2026-08-27)
- Research: docs/research/tier2-tier3-platform-constraints.md, docs/research/tts-alignment-and-tier1-platforms.md

## Context
A persona (companion, educator, commentator, brand voice) speaks for a channel and may reply to
inbound audience messages with tiered autonomy. The system is operated from the EU. Platforms
have automation and disclosure rules; some surfaces have no official API.

## Decision
- A **Persona** is a versioned, governed asset edited through the same typed-operation machinery
  as content (forms, example import, persona Revision Box → typed `PersonaRevision` diff shown
  before apply). Retroactive consistency checks propose remakes of queued/unpublished work and
  ask; published content is never silently changed.
- **Disclosure has exactly two autonomous modes**: `disclose_on_ask` (default) and `deflect`
  (never affirmatively claims to be human). **There is no autonomous claim-to-be-human mode**,
  because the EU AI Act's transparency obligations require that people interacting with an AI
  system can know it, and platform policies point the same way (X's Developer Guidelines require
  the "Automated" label and a "Bot by @…" disclosure; Meta/TikTok/YouTube automation rules are
  captured per adapter). In `human_approve_each` the human edits and sends; those messages are
  the human's own speech. The operator owns final legal compliance and the docs say so plainly.
- **PersonaFirewall is immutable code, not configuration**: real-person deny-list + PII/entity
  detectors on every outbound reply; the backstory is the only biography the model sees; fan
  messages are untrusted data with injection screening; minor-safety stops romantic engagement
  permanently for that user on any signal; crisis protocol always escalates to the human;
  exploitation limits (no soliciting money/gifts beyond official links, no unofficial platforms,
  no real-world meetings). No persona, archetype, prompt, or Revision Box request can weaken it.
- **Autonomy tiers**: `draft_only`, `human_approve_each` (default), `auto_send_low_risk`
  (classifier-gated, capped, schedule-gated, logged, kill-switchable). Answer-everything policy:
  every non-spam inbound gets a reply or a recorded skip reason. A **PersonaSchedule** (timezone,
  awake/asleep windows with jitter, delay distributions, bursts) releases sends non-metronomically
  and doubles as the rate limiter beneath platform limits.
- **Reply-API tiers (verified 2026-08-27)**: Tier 1 free/official — Discord (bot with channel
  read; `MESSAGE_CONTENT` privileged intent self-enabled under 10k servers), Telegram, Bluesky,
  Mastodon, Reddit (`/api/comment`, 100 QPM). Tier 2 within documented rules — YouTube
  `commentThreads`/`comments.insert`, Meta comment/messaging APIs (24-hour messaging window),
  Threads replies endpoints, X on pay-per-use. **No adapter** where no official API exists
  (LinkedIn member comments: `r_member_social` closed; TikTok DMs): those appear as manual-only
  inbox items with deep links.
- Anti-repetition is mandatory for companion output: OriginalityFingerprint comparison against the
  channel's own history with tighter phrase/beat thresholds plus category-rotation pressure.

## Consequences
- Engagement ships disabled (`engagement.enabled: false`) and default autonomy is
  `human_approve_each`; enabling `auto_send_low_risk` in a disclosure jurisdiction requires
  `disclose_on_ask` or a disclosed bio line.
