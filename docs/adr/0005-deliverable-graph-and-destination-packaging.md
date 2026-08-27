# ADR 0005 — Deliverable graph and destination packaging

- Status: accepted (2026-08-27)
- Research: docs/research/tier2-tier3-platform-constraints.md, docs/research/tts-alignment-and-tier1-platforms.md

## Context
Requests are campaigns of first-class deliverables (video, image, carousel, text, article,
newsletter, audio, image sequence, cover), each with destinations whose real API constraints
differ widely and change often. Nothing may be a hidden master; an X image post must never
invoke TTS or a video renderer.

## Decision
- A `ContentCampaign` compiles to a **typed dependency DAG** containing only the branches the
  selected deliverables require (shared → text / static / carousel / article / newsletter /
  image-sequence / audio / video → destination). Conditional stages return a typed `NOT_REQUIRED`
  with policy evidence instead of running.
- `DeliverableSpec` (creative intent) is separate from `DestinationPackage` (encode, crop,
  caption, disclosure, native fields, schedule, validation, immutable link to the approved
  deliverable revision). Responsive composition, not centre-cropping.
- **Destination capabilities are data, refreshed before publish**, seeded from verified official
  documentation (2026-08-27): Bluesky 300 graphemes/≤4 images (lexicon maxSize 2,000,000 B; the
  guide says 1 MB), Mastodon instance limits from `/api/v2/instance`, Discord 2000 chars / 10
  embeds / 10 MiB, Telegram 4096 chars / 1024-char captions, Instagram 100 posts/24 h and Reels
  ≤300 MB, Threads 500 chars / 250 posts/24 h / carousel 2–20 / no scheduling, TikTok unaudited
  = `SELF_ONLY` + per-post consent UX, X pay-per-use ($0.015/post, $0.20/post with URL), YouTube
  API uploads private until audit, LinkedIn no drafts/scheduling and no token refresh for
  non-partners, Pinterest trial = sandbox-only. These live in `PlatformContentProfile` records
  with a `verified_at` date, not in code constants.
- Articles map to WordPress (REST, Application Passwords), Ghost (Admin API, HS256 JWT, `source=html`),
  Markdown/HTML export. Newsletters map to Listmonk, Buttondown, Mailchimp (REST via httpx — the
  official Python SDK is stale and proprietary-licensed) as **draft campaigns by default**.
- Originality is a release gate evaluated at topic selection, script lock, adaptation compilation,
  and immediately before publication.

## Consequences
- Adding a destination means adding a capability record + adapter contract tests, never a logo.
- Package-only export is always available for every deliverable type.
