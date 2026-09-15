# Channel profile

> DRAFT 2026-09-15, derived from the repository (the wind-share demo brief in
> `fixtures/demo/campaign.json`, the `wind_2024` story, `ProjectBrief`, `EditorialStyleKit`).
> Needs the creator's review before an episode is produced. Every line marked *assumed* is a
> guess the creator should confirm or replace.

## Audience

- Curious general public, adults, not specialists (from the demo brief).
- Watches on YouTube, often on a phone: every read label must survive a 360 px wide preview.
- Comes with a question, leaves with a mechanism they can restate in one sentence.

## Topic domain (*assumed*)

How systems around us actually work, told with public data: energy and infrastructure, computing
and networks, money flows of everyday things, measurement and statistics. One specific question per
episode, one original explanatory contribution, one satisfying answer.

Out of domain: breaking news, personal finance advice, health or legal advice (YouTube's 2026
"AI personas on sensitive topics" bucket), reaction content, product reviews.

## Episode format

| Field | Value |
|---|---|
| Length | 8–12 minutes (target 10) |
| Canvas | 1920×1080, 30 fps, 16:9; optional 9:16 excerpts via the existing `ShortsPlan` |
| Language | English (`en`); Swedish (`sv`) sources are quoted in the original with a translation card |
| Narration | Creator's voice: 60–90 s recorded per episode, the rest synthesized from the creator's own reference (hypothesis to test, `docs/EDITORIAL.md`) |
| Sources on screen | Real captured pages and PDFs where a document improves the explanation; no quota |
| Commercial stance | "We are not selling anything" (demo brief). Sponsors, if any, follow `docs/SPONSORS.md` |
| Publishing | Nothing publishes from the pipeline; delivery is a reviewed export bundle |

## Original contribution, per episode

Each episode must name, in `ScriptPlan.contribution`, what the viewer could not get from the
sources alone: a calculation, a comparison, a model run, or a reframing. Retelling a source is not
an episode.

## Ranking topics

Rank by: audience question (is it asked?), evidence availability (can we source it?), original
angle, visual explainability, shelf life, production effort. Sponsor category fit is secondary.
Draft three title and thumbnail promises before production; reject any promise the evidence
cannot carry.
