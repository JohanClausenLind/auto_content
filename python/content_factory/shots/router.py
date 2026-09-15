"""Shot router for the hybrid workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from content_factory.schemas.scenes import StoryPlan
from content_factory.schemas.shots import BeatRoute, Route, ShotPlan, ShotRouting

ROUTER_VERSION = "0.1.0"


def _scene_kind_for(story: StoryPlan, beat_id: str) -> str:
    for scene in story.scenes:
        if scene.beat_id == beat_id:
            return scene.kind
    return "default"


def route_shots(
    story: StoryPlan,
    shots: ShotPlan,
    *,
    default_route: Route = "render",
    generate_kinds: Sequence[str] = (),
    overrides: Mapping[str, Route] | None = None,
) -> ShotRouting:
    """One ``BeatRoute`` per story beat, in story order."""
    overrides = dict(overrides or {})
    shot_by_beat = {sh.beat_id: sh.shot_id for sh in shots.shots if sh.beat_id}
    routed: list[BeatRoute] = []
    for beat in sorted(story.beats, key=lambda b: b.order):
        kind = _scene_kind_for(story, beat.beat_id)
        if beat.beat_id in overrides:
            route: Route = overrides[beat.beat_id]
            reason = f"override for {beat.beat_id}"
        elif kind in generate_kinds:
            route, reason = "generate", f"scene kind {kind} is in generate_kinds"
        else:
            route, reason = default_route, f"scene kind {kind}: default route"
        shot_id = shot_by_beat.get(beat.beat_id)
        if route == "generate" and shot_id is None:
            route, reason = "render", f"{reason}; no shot planned for this beat, rendering instead"
        routed.append(
            BeatRoute(
                beat_id=beat.beat_id,
                order=beat.order,
                scene_kind=kind,
                route=route,
                shot_id=shot_id if route == "generate" else None,
                reason=reason,
            )
        )
    story_hash = story.content_hash()
    shot_hash = shots.content_hash()
    digest = hashlib.sha256(f"{story_hash}|{shot_hash}|{ROUTER_VERSION}".encode()).hexdigest()[:12]
    return ShotRouting(
        routing_id=f"route_{digest}",
        deliverable_id=story.deliverable_id,
        story_plan_hash=story_hash,
        shot_plan_hash=shot_hash,
        router_version=ROUTER_VERSION,
        default_route=default_route,
        beats=tuple(routed),
    )
