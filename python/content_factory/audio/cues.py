"""The curated sound library, and a deterministic cue sheet cut from a story plan."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from content_factory.schemas.audio import CueSheet, SoundCue, SoundCueRole
from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.scenes import CompiledTimeline, StoryPlan

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY = REPO_ROOT / "assets" / "sfx"
MANIFEST_NAME = "manifest.json"


class CueError(RuntimeError):
    """A cue naming a sound the library does not have, or a library that has moved."""


@dataclass(frozen=True)
class LibrarySound:
    sound_id: str
    category: str
    path: Path
    duration_s: float
    loopable: bool
    sha256: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class SoundLibrary:
    """``assets/sfx/manifest.json``, read once and indexed by id."""

    root: Path
    sounds: dict[str, LibrarySound]
    digest: str
    """sha256 over the (id, sha256) pairs. What ties a cue sheet to the library it was cut
    against — a sound id that resolves to different bytes is a different mix, and a sheet reviewed
    against one library must not be rendered against another without saying so."""

    def get(self, sound_id: str) -> LibrarySound:
        sound = self.sounds.get(sound_id)
        if sound is None:
            msg = f"sound {sound_id!r} is not in the library at {self.root}"
            raise CueError(msg)
        return sound

    def by_category(self, category: str) -> tuple[LibrarySound, ...]:
        return tuple(s for s in self.sounds.values() if s.category == category)

    def first_with_tag(self, category: str, *tags: str) -> LibrarySound | None:
        """The first sound in a category carrying every one of `tags`, in manifest order."""
        wanted = set(tags)
        for sound in self.by_category(category):
            if wanted <= set(sound.tags):
                return sound
        return None


def load_library(root: Path | None = None) -> SoundLibrary:
    directory = root or DEFAULT_LIBRARY
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        msg = f"no sound library manifest at {manifest}"
        raise CueError(msg)
    raw = json.loads(manifest.read_text())
    sounds: dict[str, LibrarySound] = {}
    for entry in raw.get("sounds", []):
        sounds[entry["id"]] = LibrarySound(
            sound_id=entry["id"],
            category=entry["category"],
            path=directory / entry["file"],
            duration_s=float(entry["duration_s"]),
            loopable=bool(entry.get("loopable", False)),
            sha256=entry["sha256"],
            tags=tuple(entry.get("tags", ())),
        )
    digest = sha256_hex(
        canonical_dumps([[s.sound_id, s.sha256] for s in sounds.values()]).encode("utf-8")
    )
    return SoundLibrary(root=directory, sounds=sounds, digest=digest)


# Scene kind -> (library id, gain trim, why). Only kinds whose sound is unambiguous; a kind absent
# here gets nothing, because an invented sound on a quote card is worse than a silent one.
ACCENTS: dict[str, tuple[str, float, str]] = {
    "big_number": ("data_ticks", -12.0, "the figure counts up here"),
    "chart": ("chart_reveal", -12.0, "the chart draws here"),
    "bullet_sequence": ("pop_small", -15.0, "the points appear one at a time"),
    "section_intro": ("page_change", -15.0, "a new section opens"),
    "chapter_transition": ("page_change", -15.0, "a chapter turns"),
    "timeline": ("tick_light", -15.0, "the chronology steps along its spine"),
    "comparison": ("slide_transition", -15.0, "two sides arrive side by side"),
    "flow_diagram": ("counter_tick", -15.0, "the chain advances one box at a time"),
    "screenshot": ("click_soft", -18.0, "an interface is being shown"),
    "source_card": ("page_change", -18.0, "the sources are put on screen"),
}

TRANSITION_SOUND = "whoosh_soft"
"""One second, tagged `cut`/`scene-change`, and the softest of the six transitions. A documentary
cut wants a mark, not a trailer whoosh — `whoosh_deep` and `braam_horn` exist for a title."""

TRANSITION_GAIN_DB = -18.0
BED_GAIN_DB = -9.0
BED_FADE_MS = 800
ACCENT_LEAD_MS = 120
"""Accents land slightly *before* the frame the thing appears on. A sound triggered on the exact
frame reads as late, because a transient takes a few tens of milliseconds to become audible."""

MIN_SCENE_MS_FOR_TRANSITION = 800
"""Below this a scene is a flash, and a whoosh on every one of a run of them is a stutter."""


BED_TAGS_IGNORED = frozenset({"bed", "loop", "interior", "exterior"})
"""Tags that describe a sound's *use*, not its subject. Every bed is tagged `bed`, so counting it
would give every candidate the same head start; `interior`/`exterior` describe the recording, and
a subject sentence rarely uses either word about itself."""


def bed_score(words: frozenset[str], sound: LibrarySound) -> float:
    """How well a sound's tags describe these words."""
    subject = [t for t in sound.tags if t not in BED_TAGS_IGNORED]
    if not subject:
        return 0.0
    hits = sum(1 for t in subject if t in words)
    return hits / len(subject)


def bed_for(plan: StoryPlan, library: SoundLibrary) -> LibrarySound | None:
    """The ambience under the whole film, chosen from the plan's own `visual_subject`."""
    # A plan with no `visual_subject` — a lane whose film has no world described — gets no bed:
    # there is nothing to match a place ambience against, and a guess is worse than silence.
    subject = plan.visual_subject or ""
    words = frozenset(w.strip(".,;:!?()\"'").lower() for w in subject.split())
    best: tuple[float, LibrarySound] | None = None
    for category in ("place", "weather", "room-tone"):
        for sound in library.by_category(category):
            score = bed_score(words, sound)
            if score > 0 and (best is None or score > best[0]):
                best = (score, sound)
    return best[1] if best else None


@dataclass(frozen=True)
class SceneSpan:
    """One scene's place in time: what kind it is, when it starts, how long it holds."""

    scene_id: str
    kind: str
    start_ms: int
    duration_ms: int


def spans_from_timeline(plan: StoryPlan, timeline: CompiledTimeline) -> list[SceneSpan]:
    """Spans from a compiled timeline — exact frame boundaries, once one exists."""
    kinds = {s.scene_id: s.kind for s in plan.scenes}
    fps = timeline.fps
    return [
        SceneSpan(
            scene_id=c.scene_id,
            kind=kinds.get(c.scene_id, ""),
            start_ms=round(c.start_frame * 1000 / fps),
            duration_ms=round(c.duration_frames * 1000 / fps),
        )
        for c in timeline.scenes
    ]


def spans_from_beats(plan: StoryPlan, beats: Sequence[tuple[str, int, int]]) -> list[SceneSpan]:
    """Spans from the laid-out narration: ``(beat_id, start_ms, end_ms)`` per beat."""
    scenes_by_beat: dict[str, list[str]] = {}
    kinds = {}
    for scene in plan.scenes:
        scenes_by_beat.setdefault(scene.beat_id, []).append(scene.scene_id)
        kinds[scene.scene_id] = scene.kind
    spans: list[SceneSpan] = []
    for beat_id, start_ms, end_ms in beats:
        ids = scenes_by_beat.get(beat_id, [])
        if not ids:
            continue
        # Several scenes on one beat split its span evenly: the mix knows nothing finer than the
        # beat, and dividing beats stacking every accent on its first frame.
        each = max(1, (end_ms - start_ms) // len(ids))
        for index, scene_id in enumerate(ids):
            spans.append(
                SceneSpan(
                    scene_id=scene_id,
                    kind=kinds[scene_id],
                    start_ms=start_ms + each * index,
                    duration_ms=each,
                )
            )
    return spans


def cut_cue_sheet(
    plan: StoryPlan,
    spans: Sequence[SceneSpan],
    library: SoundLibrary,
    *,
    deliverable_id: str,
    total_ms: int,
    bed: bool = True,
) -> CueSheet:
    """A cue sheet for one film. Deterministic, and every cue says why it is there."""
    cues: list[SoundCue] = []
    seq = 0

    def cue_id() -> str:
        nonlocal seq
        seq += 1
        return f"cue_{seq:011d}"

    if bed:
        chosen = bed_for(plan, library)
        if chosen is not None:
            cues.append(
                SoundCue(
                    cue_id=cue_id(),
                    sound_id=chosen.sound_id,
                    role=(
                        SoundCueRole.room_tone
                        if chosen.category == "room-tone"
                        else SoundCueRole.bed
                    ),
                    at_ms=0,
                    gain_db=BED_GAIN_DB,
                    duration_ms=total_ms,
                    fade_in_ms=BED_FADE_MS,
                    fade_out_ms=BED_FADE_MS,
                    reason=(
                        f"{chosen.category} bed matching the film's subject"
                        f" ({', '.join(chosen.tags[:3])})"
                    ),
                )
            )

    for index, span in enumerate(spans):
        if span.start_ms >= total_ms:
            break
        # A mark on the cut, but not on the first scene (there is nothing to cut from) and not on
        # a flash of a scene.
        if index > 0 and span.duration_ms >= MIN_SCENE_MS_FOR_TRANSITION:
            cues.append(
                SoundCue(
                    cue_id=cue_id(),
                    sound_id=TRANSITION_SOUND,
                    role=SoundCueRole.transition,
                    at_ms=max(0, span.start_ms - ACCENT_LEAD_MS),
                    gain_db=TRANSITION_GAIN_DB,
                    scene_id=span.scene_id,
                    reason=f"cut into {span.kind or 'a scene'}",
                )
            )
        accent = ACCENTS.get(span.kind)
        if accent is None:
            continue
        sound_id, gain, why = accent
        if sound_id not in library.sounds:
            continue
        # Accents sit a little inside the scene, not on its first frame: the card's own entrance
        # animation is still running there, so a sound on frame 0 marks the cut a second time.
        at = span.start_ms + min(ACCENT_LEAD_MS * 2, max(0, span.duration_ms // 8))
        if at >= total_ms:
            continue
        cues.append(
            SoundCue(
                cue_id=cue_id(),
                sound_id=sound_id,
                role=SoundCueRole.accent,
                at_ms=at,
                gain_db=gain,
                scene_id=span.scene_id,
                reason=why,
            )
        )

    cues.sort(key=lambda c: (c.at_ms, c.cue_id))
    return CueSheet(
        deliverable_id=deliverable_id,  # type: ignore[arg-type]
        library_sha256=library.digest,
        total_ms=total_ms,
        cues=tuple(cues),
    )


def resolve(sheet: CueSheet, library: SoundLibrary) -> list[tuple[SoundCue, LibrarySound]]:
    """Pair every cue with its file, refusing a library that has moved under the sheet."""
    if sheet.library_sha256 != library.digest:
        msg = (
            "cue sheet was cut against a different sound library"
            f" ({sheet.library_sha256[:12]} vs {library.digest[:12]}). Re-cut it: a sound id that"
            " resolves to different bytes is a different mix."
        )
        raise CueError(msg)
    pairs = []
    for cue in sheet.cues:
        sound = library.get(cue.sound_id)
        if (
            cue.duration_ms is not None
            and not sound.loopable
            and cue.duration_ms > round(sound.duration_s * 1000)
        ):
            msg = (
                f"cue {cue.cue_id} runs {sound.sound_id} for {cue.duration_ms} ms, but it is"
                f" {sound.duration_s:.1f} s long and not marked loopable"
            )
            raise CueError(msg)
        pairs.append((cue, sound))
    return pairs
