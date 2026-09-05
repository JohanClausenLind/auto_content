"""TV Human Interactions ingest: 300 broadcast clips, the only kissing and the only stated angles.

Every number comes from ``_index/measured/tvhi.json``, which already counted each clip's
annotation file: the per-frame interaction labels, the per-person head orientations, and how many
boxes each frame carries. The ``.annotations`` files are opened here only to digest them, so this
module cannot disagree with the measured index about what they contain.

Three decisions are worth reading.

**Head orientation becomes ``camera_angles``.** This is the one source on disk that states which
way each head faces, and that is exactly the angle information the rest of the library lacks. The
dataset's own five values are kept verbatim rather than bucketed into ``front`` or ``side``,
because bucketing would be this module's invention and the raw values are more specific. They are
ordered most-annotated first, with ties broken by name so the order is stable.

**The 100 negative clips are emitted.** A clip with nothing but ``no_interaction`` gets
``interaction_tags = ("no_contact",)`` and no contact tags, which the contract allows. A negative
example is worth having: it is two people in one broadcast frame not touching, which is the
staging either side of every interaction.

**``people_count`` is the largest ``num_bbxs`` any frame of the clip carries.** A frame with fewer
boxes means somebody walked out of shot, not that the cast shrank, and 53 clips would otherwise be
recorded as one-person clips because the pair is only both in frame for part of the take. The
modal, smallest and largest counts are all kept in ``measured``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

from content_factory.schemas.base import file_sha256
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    FileRole,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceSource,
    UsageClass,
)

INGESTER_VERSION = "0.1.0"
SOURCE = ReferenceSource.tv_human_interactions

ANNOTATION_DIR = "TV-Human-Interactions/raw/tv_human_interaction_annotations"

_INDEX_FILES = ("class_maps.json", "tvhi.json")

_CLIP_RE = re.compile(r"^(?P<name>[A-Za-z]+)_(?P<number>\d{4})$")

NO_INTERACTION = "no_interaction"
"""The dataset's own name for a frame where the annotated people are not interacting."""

# Real broadcast footage of real people, and unlike Harmony4D nothing stands between the lens and
# the subjects, so the pixels are usable as a visual reference. The resolution is the limit, and it
# is carried by width and height rather than by a usage class.
SETTING = "broadcast television"
USAGE = UsageClass.pixels_usable


class _InteractionSpec(NamedTuple):
    """How one annotated interaction value lands in the contract's closed vocabularies."""

    tag: InteractionTag
    contact: tuple[ContactTag, ...]
    affection: Affection


_SPECS: dict[str, _InteractionSpec] = {
    "hand_shake": _InteractionSpec(
        InteractionTag.handshake, (ContactTag.hands,), Affection.affection
    ),
    "high_five": _InteractionSpec(
        InteractionTag.high_five, (ContactTag.hands,), Affection.affection
    ),
    "hug": _InteractionSpec(
        InteractionTag.hug, (ContactTag.back, ContactTag.torso), Affection.affection
    ),
    "kiss": _InteractionSpec(InteractionTag.kiss, (ContactTag.face,), Affection.affection),
}

# The clip filename's class, lowercased into something a clip_id can hold. The annotations, not
# this, decide the tags: the class is only cross-checked against them.
_CLASS_WORDS = {
    "handShake": "hand_shake",
    "highFive": "high_five",
    "hug": "hug",
    "kiss": "kiss",
    "negative": "negative",
}


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every TV-HI clip this source contributes, plus one line per thing skipped and why.

    Guarantees: clips are sorted by ``clip_id``; the same ``root`` and ``ingested_at`` produce
    byte-identical clips, because every count comes from the measured index and every digest from
    the file itself; every interaction and orientation value is checked against
    ``class_maps.json`` before it is used, so an unknown value is reported rather than mapped onto
    a near-miss tag; and a clip is emitted only when its video is on disk.
    """
    index, failure = _load_index(root / "_index" / "measured")
    if index is None:
        return [], [failure or "tvhi: the measured index could not be read"]

    verified = index["class_maps.json"].get("tv_human_interactions", {})
    known_interactions = frozenset(verified.get("interaction_values", {}))
    known_orientations = frozenset(verified.get("head_orientation_values", {}))

    skipped: list[str] = []
    clips: list[ReferenceClip] = []
    for entry in sorted(index["tvhi.json"], key=lambda e: str(e["clip"])):
        clip = _clip(root, entry, known_interactions, known_orientations, ingested_at, skipped)
        if clip is not None:
            clips.append(clip)
    clips.sort(key=lambda clip: clip.clip_id)
    return clips, skipped


def _load_index(measured: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The measured JSON this ingester reads, or a reason it could not be read.

    A host without ``/mnt/fast/reference`` is a normal condition for this repo, so a missing index
    is one readable line in the library manifest rather than a traceback in the build driver.
    """
    out: dict[str, Any] = {}
    for name in _INDEX_FILES:
        path = measured / name
        try:
            out[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, f"tvhi: {path} could not be read, so nothing was ingested"
    return out, None


def _reference_file(root: Path, relative: str, role: FileRole) -> ReferenceFile | None:
    """One ``ReferenceFile`` with a real digest, or None when the file is not on disk."""
    path = root / relative
    if not path.is_file():
        return None
    return ReferenceFile(
        role=role,
        path=relative,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
    )


def _counts(raw: Any) -> dict[str, int]:
    """A measured count map with its values as ints, ordered by name so iteration is stable."""
    return {str(key): int(value) for key, value in sorted(raw.items())}


def _people(raw: Any) -> tuple[int, int, int]:
    """The largest, smallest and modal ``num_bbxs`` over a clip's annotated frames."""
    counts = {int(key): int(value) for key, value in raw.items()}
    modal = max(sorted(counts), key=lambda people: (counts[people], people))
    return max(counts), min(counts), modal


def _angles(orientations: dict[str, int]) -> tuple[str, ...]:
    """The clip's head orientations, most annotated first, ties broken by name."""
    return tuple(sorted(orientations, key=lambda name: (-orientations[name], name)))


def _clip(
    root: Path,
    entry: dict[str, Any],
    known_interactions: frozenset[str],
    known_orientations: frozenset[str],
    ingested_at: str,
    skipped: list[str],
) -> ReferenceClip | None:
    """One clip, or None with a reason appended to ``skipped``."""
    name = str(entry["clip"])
    match = _CLIP_RE.match(name)
    class_word = _CLASS_WORDS.get(str(entry["class"]))
    if match is None or class_word is None:
        skipped.append(
            f"tvhi {name}: class {entry['class']!r} or the clip name is not one this ingester"
            " knows how to name, so the clip is dropped"
        )
        return None

    labels = _counts(entry["labels"])
    orientations = _counts(entry["orientations"])
    unknown = sorted((set(labels) | set(orientations)) - known_interactions - known_orientations)
    if unknown:
        skipped.append(
            f"tvhi {name}: annotation values {unknown} are not in class_maps.json, so the clip is"
            " dropped rather than mapped onto a near-miss tag"
        )
        return None

    positives = [value for value in sorted(labels) if value != NO_INTERACTION]
    unmapped = [value for value in positives if value not in _SPECS]
    if unmapped:
        skipped.append(
            f"tvhi {name}: interaction {unmapped} has no tag in the contract's vocabulary, so the"
            " clip is dropped"
        )
        return None
    if class_word != "negative" and class_word not in positives:
        skipped.append(
            f"tvhi {name}: the filename says {class_word} but its annotation labels are"
            f" {sorted(labels)}, so the annotation is what the tags were taken from"
        )

    specs = [_SPECS[value] for value in positives]
    if specs:
        tags = tuple(sorted({spec.tag for spec in specs}, key=lambda tag: tag.value))
        contact = tuple(
            sorted({tag for spec in specs for tag in spec.contact}, key=lambda tag: tag.value)
        )
        affection = Affection.affection
    else:
        # A negative clip: two annotated people who never touch. The contract allows an empty
        # contact tuple when no_contact is the only interaction, which is exactly this case.
        tags = (InteractionTag.no_contact,)
        contact = ()
        affection = Affection.neutral

    video = _reference_file(root, str(entry["file"]), "video")
    if video is None:
        skipped.append(f"tvhi {name}: {entry['file']} is not on disk")
        return None
    annotation = _reference_file(root, f"{ANNOTATION_DIR}/{name}.annotations", "bbox")
    if annotation is None:
        skipped.append(
            f"tvhi {name}: {ANNOTATION_DIR}/{name}.annotations is not on disk, so the clip keeps"
            " its measured tags but carries no pose information"
        )

    largest, smallest, modal = _people(entry["people_per_frame"])
    measured: dict[str, float | list[float]] = {
        "annotated_frames": float(entry["ann_frames"]),
        "people_largest": float(largest),
        "people_smallest": float(smallest),
        "people_modal": float(modal),
    }
    for value, count in labels.items():
        measured[f"person_frames_{value}"] = float(count)
    for value, count in orientations.items():
        measured[f"orient_person_frames_{value}"] = float(count)

    files = (video, annotation) if annotation is not None else (video,)
    return ReferenceClip(
        clip_id=f"tvhi_{class_word}_{match.group('number')}",
        source=SOURCE,
        source_ref=name,
        modality=Modality.video_rgb,
        usage=USAGE,
        people_count=largest,
        affection=affection,
        interaction_tags=tags,
        contact_tags=contact,
        # The dataset annotates boxes, interaction and head orientation, and no posture at all.
        # Standing is the assumption for a shot-length broadcast two-person interaction, and the
        # caption says it was assumed so a consumer is not misled into trusting it.
        postures=(Posture.standing,),
        setting=SETTING,
        camera_angles=_angles(orientations),
        frame_count=int(entry["nb_frames"]),
        native_fps=float(entry["fps"]),
        duration_s=float(entry["duration_s"]),
        width=int(entry["width"]),
        height=int(entry["height"]),
        pose_format="bbox_only" if annotation is not None else "none",
        pose_root=f"{ANNOTATION_DIR}/{name}.annotations" if annotation is not None else None,
        caption=_caption(name, labels, orientations, largest),
        caption_source="parsed_index",
        measured=measured,
        files=files,
        ingested_at=ingested_at,
        ingester_version=INGESTER_VERSION,
    )


def _caption(name: str, labels: dict[str, int], orientations: dict[str, int], people: int) -> str:
    """A sentence that says what was annotated, how often, and which way the heads face."""
    total = sum(labels.values())
    positives = {value: count for value, count in labels.items() if value != NO_INTERACTION}
    heads = ", ".join(_angles(orientations))
    if positives:
        listed = ", ".join(
            f"{value.replace('_', ' ')} on {count} of {total} person-frames"
            for value, count in sorted(positives.items())
        )
        what = f"{people} people, {listed}"
    else:
        what = (
            f"{people} people and no interaction in any of its {total} person-frames, a negative"
            " example"
        )
    return (
        f"TV-Human-Interactions {name}, broadcast television: {what}. Heads face {heads}."
        " Posture is not annotated by this dataset."
    )
