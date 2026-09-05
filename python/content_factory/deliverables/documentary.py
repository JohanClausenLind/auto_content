"""Documentary episode lane: outline planning, campaign shape, and shorts derivation.

Everything here is deterministic: the same brief, duration and shorts count produce the same
campaign, outline and excerpts. Shorts are re-edits, not crops — each excerpt is a contiguous,
self-contained run of long-form beats that becomes its own vertical StoryPlan, so narration,
alignment, captions and layout all regenerate per short through the normal audio/video branches.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from content_factory.schemas.content import (
    AspectRatio,
    ContentCampaign,
    ContentDeliverable,
    DeliverableRelationship,
    InputMode,
    LongVideoSpec,
    ProjectBrief,
    ShortVideoSpec,
)
from content_factory.schemas.documentary import (
    SECTION_ORDER,
    ChapterMarker,
    EpisodeMetadata,
    EpisodeOutline,
    EpisodeSection,
    EpisodeSectionKind,
    ShortExcerpt,
    ShortsPlan,
)
from content_factory.schemas.research import SourceRecord
from content_factory.schemas.scenes import StoryPlan, VisualBeat

DEFAULT_AUDIENCE = (
    "Curious adults interested in science, mathematics, computing, technology, "
    "infrastructure, and how systems work"
)

# Reference arc for a 600 s episode; other durations scale these boundaries proportionally.
_REFERENCE_BOUNDS_S: tuple[int, ...] = (0, 20, 50, 180, 390, 495, 555, 600)

_SECTION_PURPOSE: dict[EpisodeSectionKind, str] = {
    EpisodeSectionKind.cold_open: (
        "Show a concrete surprise, paradox, failure, or measurable consequence — no definitions."
    ),
    EpisodeSectionKind.question_stakes: (
        "State what the viewer will understand and why it matters."
    ),
    EpisodeSectionKind.build_model: "Introduce the minimum concepts and visual vocabulary.",
    EpisodeSectionKind.run_system: (
        "Animate the mechanism, algorithm, experiment, or causal chain."
    ),
    EpisodeSectionKind.change_variable: (
        "Compare scenarios with a chart, simulation, or counterfactual."
    ),
    EpisodeSectionKind.show_limits: ("Limits, uncertainty, edge cases, or a common misconception."),
    EpisodeSectionKind.synthesis: (
        "Resolve the opening question; end with one memorable implication, not a summary list."
    ),
}

_CHAPTER_TITLE: dict[EpisodeSectionKind, str] = {
    EpisodeSectionKind.cold_open: "Opening",
    EpisodeSectionKind.question_stakes: "The question",
    EpisodeSectionKind.build_model: "Building the model",
    EpisodeSectionKind.run_system: "Running the system",
    EpisodeSectionKind.change_variable: "Changing one variable",
    EpisodeSectionKind.show_limits: "Where it breaks",
    EpisodeSectionKind.synthesis: "What it means",
}

_ASPECT_DIMS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.r16x9: (1920, 1080),
    AspectRatio.r9x16: (1080, 1920),
    AspectRatio.r1x1: (1080, 1080),
    AspectRatio.r4x5: (1080, 1350),
}

SHORT_MIN_MS = 20_000
SHORT_MAX_MS = 60_000


def _id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:12]}"


def episode_outline(
    *, deliverable_id: str, target_duration_s: int, words_per_minute: int = 145
) -> EpisodeOutline:
    """Scale the reference arc to the target duration; word budgets follow time share exactly."""
    ref_total = _REFERENCE_BOUNDS_S[-1]
    bounds = [round(target_duration_s * b / ref_total) for b in _REFERENCE_BOUNDS_S]
    for i in range(1, len(bounds)):  # a very short episode must still keep every section
        if bounds[i] <= bounds[i - 1]:
            bounds[i] = bounds[i - 1] + 1
    bounds[-1] = target_duration_s

    total_words = round(target_duration_s * words_per_minute / 60)
    spans = [bounds[i + 1] - bounds[i] for i in range(len(SECTION_ORDER))]
    exact = [total_words * s / target_duration_s for s in spans]
    budgets = [max(1, int(x)) for x in exact]
    remainders = sorted(
        range(len(exact)), key=lambda i: (exact[i] - int(exact[i]), -i), reverse=True
    )
    k = 0
    while sum(budgets) < total_words:
        budgets[remainders[k % len(remainders)]] += 1
        k += 1
    sections = tuple(
        EpisodeSection(
            kind=kind,
            purpose=_SECTION_PURPOSE[kind],
            start_s=bounds[i],
            end_s=bounds[i + 1],
            word_budget=budgets[i],
        )
        for i, kind in enumerate(SECTION_ORDER)
    )
    return EpisodeOutline(
        outline_id=_id("out", deliverable_id, target_duration_s, words_per_minute),
        deliverable_id=deliverable_id,
        target_duration_s=target_duration_s,
        words_per_minute=words_per_minute,
        sections=sections,
    )


def documentary_campaign(
    *,
    workspace_id: str,
    topic: str,
    objective: str = "",
    audience: str = DEFAULT_AUDIENCE,
    minutes: int = 10,
    shorts: int = 3,
    music: bool = True,
    fps: int = 30,
    source_urls: Sequence[str] = (),
    brief: ProjectBrief | None = None,
) -> ContentCampaign:
    """One long-form 16:9 episode plus N vertical shorts linked as excerpt_of the long form."""
    if brief is None:
        brief = ProjectBrief(
            brief_id=_id("brf", workspace_id, topic, minutes, shorts),
            workspace_id=workspace_id,
            input_mode=InputMode.brief_first,
            topic=topic,
            objective=objective or f"Explain {topic} accurately, mechanism first.",
            audience=audience,
            source_urls=tuple(source_urls),
        )
    campaign_id = _id("cmp", brief.brief_id, minutes, shorts)
    long_id = _id("dlv", campaign_id, "long")
    duration_s = minutes * 60
    long_spec = LongVideoSpec(
        deliverable_id=long_id,
        title=f"{topic} — episode"[:200],
        aspect=AspectRatio.r16x9,
        target_duration_s=(duration_s * 9 // 10, duration_s * 11 // 10),
        fps=fps,  # type: ignore[arg-type]
        narration=True,
        music=music,
        captions="sidecar",
        chapters=True,
    )
    deliverables: list[ContentDeliverable] = [long_spec]
    relationships: list[DeliverableRelationship] = []
    for i in range(shorts):
        short_id = _id("dlv", campaign_id, "short", i)
        deliverables.append(
            ShortVideoSpec(
                deliverable_id=short_id,
                title=f"{topic} — short {i + 1}"[:200],
                aspect=AspectRatio.r9x16,
                target_duration_s=(20, 60),
                fps=fps,  # type: ignore[arg-type]
                narration=True,
                music=music,
                captions="burned_in",
            )
        )
        relationships.append(
            DeliverableRelationship(from_id=short_id, to_id=long_id, kind="excerpt_of")
        )
    return ContentCampaign(
        campaign_id=campaign_id,
        workspace_id=workspace_id,
        brief=brief,
        deliverables=tuple(deliverables),
        relationships=tuple(relationships),
    )


def find_documentary(
    campaign: ContentCampaign,
) -> tuple[LongVideoSpec, tuple[ShortVideoSpec, ...]] | None:
    """The documentary shape: a long_video plus the short_videos that are excerpts of it."""
    long_spec = next((d for d in campaign.deliverables if d.type == "long_video"), None)
    if long_spec is None:
        return None
    assert isinstance(long_spec, LongVideoSpec)
    excerpt_ids = {
        r.from_id
        for r in campaign.relationships
        if r.kind == "excerpt_of" and r.to_id == long_spec.deliverable_id
    }
    shorts = tuple(
        d
        for d in campaign.deliverables
        if d.type == "short_video" and d.deliverable_id in excerpt_ids
    )
    return long_spec, shorts  # type: ignore[return-value]


def _beat_duration_ms(beat: VisualBeat) -> int:
    if beat.measured_start_ms is not None and beat.measured_end_ms is not None:
        return beat.measured_end_ms - beat.measured_start_ms
    if beat.planned_duration_ms is None:
        msg = f"beat {beat.beat_id} has neither measured nor planned duration"
        raise ValueError(msg)
    return beat.planned_duration_ms


def plan_shorts(
    long_plan: StoryPlan,
    short_ids: Sequence[str],
    *,
    min_ms: int = SHORT_MIN_MS,
    max_ms: int = SHORT_MAX_MS,
    hooks: Mapping[str, str] | None = None,
) -> ShortsPlan:
    """Pick one contiguous beat window per short: self-contained, 20-60 s, claim-dense first.

    Windows never split a beat mid-sentence, prefer starting on a claim-bearing beat, and stay
    disjoint while enough disjoint candidates exist. When the whole long plan is shorter than
    ``min_ms`` the only honest excerpt is the full plan; that fallback is used for every short
    rather than inventing content.
    """
    hooks = dict(hooks or {})
    beats = sorted(long_plan.beats, key=lambda b: b.order)
    durs = [_beat_duration_ms(b) for b in beats]

    candidates: list[tuple[int, int, int]] = []  # (start_index, end_index_inclusive, total_ms)
    for i in range(len(beats)):
        total = 0
        for j in range(i, len(beats)):
            total += durs[j]
            if total > max_ms:
                break
            if total >= min_ms:
                candidates.append((i, j, total))
    if not candidates:
        candidates = [(0, len(beats) - 1, sum(durs))]

    def _claims(i: int, j: int) -> int:
        return sum(len(b.claim_ids) for b in beats[i : j + 1])

    ranked = sorted(
        candidates,
        key=lambda c: (
            -_claims(c[0], c[1]),
            -int(bool(beats[c[0]].claim_ids)),  # a hook that opens on evidence
            -c[2],
            c[0],
            c[1],
        ),
    )
    chosen: list[tuple[int, int, int]] = []
    for cand in ranked:  # disjoint first
        if len(chosen) == len(short_ids):
            break
        if all(cand[1] < c[0] or cand[0] > c[1] for c in chosen):
            chosen.append(cand)
    for cand in ranked:  # then overlaps, then repeats — deterministic, never invented
        if len(chosen) == len(short_ids):
            break
        if cand not in chosen:
            chosen.append(cand)
    while chosen and len(chosen) < len(short_ids):
        chosen.append(chosen[len(chosen) % len(ranked)])
    chosen.sort(key=lambda c: (c[0], c[1]))

    excerpts = []
    for short_id, (i, j, total) in zip(short_ids, chosen, strict=True):
        window = beats[i : j + 1]
        claim_ids = tuple(dict.fromkeys(c for b in window for c in b.claim_ids))
        excerpts.append(
            ShortExcerpt(
                short_deliverable_id=short_id,
                source_beat_ids=tuple(b.beat_id for b in window),
                hook_text=hooks.get(short_id, window[0].display_text),
                duration_ms=total,
                claim_ids=claim_ids,
            )
        )
    return ShortsPlan(
        plan_id=_id("plan", long_plan.plan_id, "shorts", *short_ids),
        long_deliverable_id=long_plan.deliverable_id,
        excerpts=tuple(excerpts),
    )


def short_story_plan(
    long_plan: StoryPlan,
    excerpt: ShortExcerpt,
    *,
    width: int = 1080,
    height: int = 1920,
) -> StoryPlan:
    """A fresh vertical plan from the excerpt's beats: narration timings reset (each short is
    re-synthesized and re-aligned), scenes re-anchored, a rewritten hook loses claim links."""
    wanted = set(excerpt.source_beat_ids)
    beats = [b for b in sorted(long_plan.beats, key=lambda b: b.order) if b.beat_id in wanted]
    if {b.beat_id for b in beats} != wanted:
        missing = sorted(wanted - {b.beat_id for b in beats})
        msg = f"excerpt references beats missing from the long plan: {missing}"
        raise ValueError(msg)
    scenes = tuple(s for s in long_plan.scenes if s.beat_id in wanted)
    sceneless = sorted(wanted - {s.beat_id for s in scenes})
    if sceneless:
        msg = f"excerpt beats have no scene to carry them: {sceneless}"
        raise ValueError(msg)

    rebuilt: list[VisualBeat] = []
    for order, beat in enumerate(beats):
        hook_rewritten = order == 0 and excerpt.hook_text != beat.display_text
        rebuilt.append(
            VisualBeat(
                beat_id=beat.beat_id,
                order=order,
                display_text=excerpt.hook_text if order == 0 else beat.display_text,
                spoken_text=None if hook_rewritten else beat.spoken_text,
                claim_ids=() if hook_rewritten else beat.claim_ids,
                planned_duration_ms=max(200, _beat_duration_ms(beat)),
            )
        )
    return StoryPlan(
        plan_id=_id("plan", long_plan.plan_id, excerpt.short_deliverable_id),
        deliverable_id=excerpt.short_deliverable_id,
        fps=long_plan.fps,
        width=width,
        height=height,
        beats=tuple(rebuilt),
        scenes=scenes,
        handle_ms=long_plan.handle_ms,
        min_scene_ms=long_plan.min_scene_ms,
        # The excerpt's own line, burned over the opening seconds: a muted viewer decides in the
        # first 1-3 s, and the headline for a 30-second excerpt is not the episode's headline.
        hook_text=excerpt.hook_text[:120],
        # The world is the same film's, so the shorts' anchors are drawn in the same place. It is
        # not editorial text and is not re-edited per excerpt.
        visual_subject=long_plan.visual_subject,
    )


def aspect_dimensions(aspect: AspectRatio) -> tuple[int, int]:
    return _ASPECT_DIMS[aspect]


def aspect_or_portrait(value: object) -> AspectRatio:
    """A deliverable spec's aspect when it has one, else 9:16.

    Not every spec carries an aspect: an article and a newsletter have no picture. A caller that
    works across the whole spec union therefore needs somewhere for "no aspect was asked for" to
    land, and it should be a checked value rather than a cast that would let a typo through.
    """
    for aspect in _ASPECT_DIMS:
        if value == aspect:
            return aspect
    return AspectRatio.r9x16


def chapter_markers(outline: EpisodeOutline) -> tuple[ChapterMarker, ...]:
    return tuple(
        ChapterMarker(at_s=s.start_s, title=_CHAPTER_TITLE[s.kind]) for s in outline.sections
    )


DISCLOSURE_TEXT = (
    "Some imagery in this video was generated with AI models and is illustrative, "
    "not documentary evidence."
)


def episode_metadata(
    brief: ProjectBrief,
    outline: EpisodeOutline,
    sources: Sequence[SourceRecord] = (),
    *,
    uses_generated_media: bool = True,
) -> EpisodeMetadata:
    """Deterministic publishing metadata. Nothing here uploads; distribution gates stay in force."""
    topic = brief.topic.strip()
    chapters = chapter_markers(outline)
    attribution = tuple(
        " — ".join(
            part
            for part in (
                s.title or s.canonical_url,
                s.publisher,
                f"{s.canonical_url} (accessed {s.accessed_at})",
            )
            if part
        )
        for s in sources
    )
    lines = [brief.objective.strip() or topic, "", "Chapters:"]
    lines += [f"{c.at_s // 60}:{c.at_s % 60:02d} {c.title}" for c in chapters]
    if attribution:
        lines += ["", "Sources:"]
        lines += [f"- {a}" for a in attribution]
    if uses_generated_media:
        lines += ["", DISCLOSURE_TEXT]
    words = [w.strip(".,:;!?()[]").lower() for w in topic.split()]
    tags = tuple(dict.fromkeys(w for w in words if len(w) >= 4))[:10]
    return EpisodeMetadata(
        deliverable_id=outline.deliverable_id,
        title_candidates=(
            topic[:100],
            f"{topic}, explained"[:100],
            f"{topic}: what the evidence shows"[:100],
        ),
        description="\n".join(lines)[:5000],
        chapters=chapters,
        tags=tags,
        attribution=attribution,
        synthetic_media_disclosure=uses_generated_media,
        disclosure_text=DISCLOSURE_TEXT if uses_generated_media else "",
    )
