"""UT-Interaction ingest: 120 segmented clips plus the 20 uncut sequences they were cut from.

Everything this module needs was already measured into ``_index/measured``, so nothing here
decodes video or opens the spreadsheet: ``ut_segmented.json``, ``ut_sequences.json`` and
``ut_labels.json`` carry the container numbers and the 146 annotation rows, and
``class_maps.json`` carries the class map that was verified by looking at a frame per class. Only
the mapping from a verified dataset label onto the contract's closed tag vocabulary lives in code,
and it is cross-checked against ``class_maps.json`` so the two can never drift apart silently.

Two decisions are worth reading.

**The uncut sequences are emitted, not skipped.** They are content supersets of the segmented
clips, so they duplicate material, but they are the only footage on disk that shows two people
walking into position, acting, and leaving again, and the spreadsheet's frame ranges are only
meaningful against them. They carry ``affection = aggression`` because each one contains a punch, a
kick and a push, which keeps a query for tenderness from being answered with a whole take that has
a shove in the middle.

**The bbox is a point.** The spreadsheet gives one X/Y coordinate and a main actor per action, not
a rectangle, so the coordinate is recorded in ``measured`` and no key pose is invented from it.
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
SOURCE = ReferenceSource.ut_interaction

LABELS_XLS = "UT-Interaction/raw/ut-interaction_labels_110912.xls"
"""The one annotation file for all 140 clips, so its digest is computed once and shared."""

_INDEX_FILES = ("class_maps.json", "ut_segmented.json", "ut_sequences.json", "ut_labels.json")

_SEQUENCE_RE = re.compile(r"^seq(\d{1,2})$")

# Verified by the contact sheet, not by a paper: sheets/ut_classes.png shows a fixed high-angle
# camera on a concrete parking lot, two men in everyday clothes, bright daylight. Nothing stands
# between the lens and the subjects, so the pixels are usable as a visual reference.
SETTING = "outdoor concrete parking lot, daylight"
CAMERA_ANGLES = ("high_angle",)
USAGE = UsageClass.pixels_usable


class _ClassSpec(NamedTuple):
    """How one verified UT class label lands in the contract's closed vocabularies."""

    tag: InteractionTag
    contact: tuple[ContactTag, ...]
    affection: Affection


class _Class(NamedTuple):
    """One verified class id: the dataset's own label plus where it lands in the vocabularies."""

    label: str
    spec: _ClassSpec


# Keyed by the label in class_maps.json. The four non-affectionate UT classes are all in the
# contract's aggression block, point included: in this footage pointing is a staged confrontation,
# which is why class_maps.json records it as not affectionate.
_SPECS: dict[str, _ClassSpec] = {
    "hand_shake": _ClassSpec(InteractionTag.handshake, (ContactTag.hands,), Affection.affection),
    "hug": _ClassSpec(InteractionTag.hug, (ContactTag.back, ContactTag.torso), Affection.affection),
    # A kick lands with the foot, and the vocabulary has no foot or leg contact, so this records
    # the receiving end only. Reported as a vocabulary gap rather than approximated with a tag.
    "kick": _ClassSpec(InteractionTag.kick, (ContactTag.torso,), Affection.aggression),
    "point": _ClassSpec(InteractionTag.point, (ContactTag.none,), Affection.aggression),
    "punch": _ClassSpec(
        InteractionTag.punch, (ContactTag.hands, ContactTag.torso), Affection.aggression
    ),
    "push": _ClassSpec(
        InteractionTag.push, (ContactTag.hands, ContactTag.torso), Affection.aggression
    ),
}

_ACTOR_WORDS = {-1: "both", 0: "the left person", 1: "the right person"}


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every UT-Interaction clip this source contributes, plus one line per thing skipped and why.

    Guarantees: clips are sorted by ``clip_id``; the same ``root`` and ``ingested_at`` produce
    byte-identical clips, because every number comes from the measured index and every digest from
    the file itself; no clip is emitted whose tags disagree with ``class_maps.json``; and a
    segmented clip with no spreadsheet row is still emitted, with ``pose_format = "none"`` and a
    line in the skipped list saying its annotation is the thing that is missing.
    """
    index, failure = _load_index(root / "_index" / "measured")
    if index is None:
        return [], [failure or "ut: the measured index could not be read"]

    skipped: list[str] = []
    classes, class_notes = _verified_classes(index["class_maps.json"])
    skipped.extend(class_notes)
    if not classes:
        return [], skipped

    labels = index["ut_labels.json"]
    primary, others = _split_label_rows(labels)
    xls = _reference_file(root, LABELS_XLS, "bbox")
    if xls is None:
        skipped.append(
            f"ut: {LABELS_XLS} is not on disk, so no clip carries the annotation it was labelled"
            " from and every clip is emitted with pose_format none"
        )

    clips: list[ReferenceClip] = []
    clips.extend(_segmented_clips(root, index, classes, primary, xls, ingested_at, skipped))
    clips.extend(_sequence_clips(root, index, classes, primary, others, xls, ingested_at, skipped))
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
            return None, f"ut: {path} could not be read, so nothing was ingested"
    return out, None


def _verified_classes(class_maps: Any) -> tuple[dict[int, _Class], list[str]]:
    """The class-id to tag map, taken from ``class_maps.json`` and checked against ``_SPECS``.

    Guarantees a class id is used only when the verified label is one this module knows and the
    verified affection flag agrees with the tag table. Anything else is dropped with a reason,
    because a wrong entry here answers a search for a hug with a shove.
    """
    classes = class_maps.get("ut_interaction", {}).get("classes", {})
    verified: dict[int, _Class] = {}
    notes: list[str] = []
    for class_id, entry in sorted(classes.items(), key=lambda kv: int(kv[0])):
        label = entry.get("label", "")
        spec = _SPECS.get(label)
        if spec is None:
            notes.append(
                f"ut class {class_id}: class_maps.json calls it {label!r}, which this ingester has"
                " no tag for, so its clips are dropped"
            )
            continue
        if bool(entry.get("affection")) != (spec.affection is Affection.affection):
            notes.append(
                f"ut class {class_id} ({label}): class_maps.json affection"
                f" {entry.get('affection')!r} disagrees with the tag table's"
                f" {spec.affection.value}, so its clips are dropped"
            )
            continue
        verified[int(class_id)] = _Class(label, spec)
    return verified, notes


def _split_label_rows(labels: Any) -> tuple[dict[int, list[Any]], dict[int, int]]:
    """The spreadsheet split at its own ``others:`` marker row.

    Rows above the marker are the 119 primary annotations, one per segmented clip in file order.
    Rows below it are 26 further action instances in the continuous sequences that were never cut
    into a segmented clip. Returns the primary rows grouped by sequence number in listed order,
    and a count of the extra rows per sequence.
    """
    primary: dict[int, list[Any]] = {}
    others: dict[int, int] = {}
    after_marker = False
    for row in labels:
        match = _SEQUENCE_RE.match(str(row.get("sequence #", "")))
        if match is None:
            after_marker = True
            continue
        sequence = int(match.group(1))
        if after_marker:
            others[sequence] = others.get(sequence, 0) + 1
        else:
            primary.setdefault(sequence, []).append(row)
    return primary, others


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


def _as_int(value: Any) -> int | None:
    """The spreadsheet's numbers as ints. Blank cells come through as ``''`` and give None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _paired_rows(clips: list[Any], rows: list[Any]) -> list[Any | None]:
    """One spreadsheet row per segmented clip, paired by position and checked by class id.

    The spreadsheet lists a sequence's actions in the same order as that sequence's segmented
    clips, and the segmented clips carry their class in the filename, so the pairing is verifiable:
    it stops at the first position where the two class ids disagree rather than sliding every
    later row onto the wrong clip.
    """
    paired: list[Any | None] = []
    aligned = True
    for position, clip in enumerate(clips):
        row = rows[position] if aligned and position < len(rows) else None
        if row is not None and _as_int(row.get("Activity Class ID")) != clip["field_c"]:
            aligned = False
            row = None
        paired.append(row)
    return paired


def _segmented_clips(
    root: Path,
    index: dict[str, Any],
    classes: dict[int, _Class],
    primary: dict[int, list[Any]],
    xls: ReferenceFile | None,
    ingested_at: str,
    skipped: list[str],
) -> list[ReferenceClip]:
    """One clip per segmented AVI on disk. Appends a reason to ``skipped`` for each one dropped."""
    by_sequence: dict[int, list[Any]] = {}
    for entry in index["ut_segmented.json"]:
        by_sequence.setdefault(entry["field_b"], []).append(entry)

    out: list[ReferenceClip] = []
    for sequence in sorted(by_sequence):
        entries = sorted(by_sequence[sequence], key=lambda e: e["field_a"])
        rows = primary.get(sequence, [])
        for entry, row in zip(entries, _paired_rows(entries, rows), strict=True):
            known = classes.get(entry["field_c"])
            if known is None:
                skipped.append(
                    f"ut {entry['stem']}: class id {entry['field_c']} is not a usable class,"
                    " so the clip is dropped"
                )
                continue
            video = _reference_file(root, entry["file"], "video")
            if video is None:
                skipped.append(f"ut {entry['stem']}: {entry['file']} is not on disk")
                continue
            label, spec = known.label, known.spec
            start = _as_int(row.get("Starting Frame #")) if row else None
            end = _as_int(row.get("Ending Frame #")) if row else None
            actor = _as_int(row.get("Main actor (left=0, right=1)")) if row else None
            x = _as_int(row.get("X-Cord")) if row else None
            y = _as_int(row.get("Y-Cord")) if row else None
            if row is None:
                skipped.append(
                    f"ut {entry['stem']}: no row for it above the spreadsheet's 'others:' marker,"
                    " so the clip is emitted with pose_format none and no bbox"
                )
            suffix = str(start) if start is not None else f"clip{entry['field_a']}"
            measured: dict[str, float | list[float]] = {}
            if start is not None and end is not None:
                measured["sequence_start_frame"] = float(start)
                measured["sequence_end_frame"] = float(end)
                measured["annotated_frames"] = float(end - start + 1)
            if x is not None and y is not None:
                measured["actor_x_px"] = float(x)
                measured["actor_y_px"] = float(y)
            if actor is not None:
                measured["main_actor"] = float(actor)
            files = (video, xls) if row is not None and xls is not None else (video,)
            out.append(
                ReferenceClip(
                    clip_id=f"ut_seq{sequence}_{label}_{suffix}",
                    source=SOURCE,
                    source_ref=entry["file"].removeprefix("UT-Interaction/raw/"),
                    modality=Modality.video_rgb,
                    usage=USAGE,
                    people_count=2,
                    affection=spec.affection,
                    interaction_tags=(spec.tag,),
                    contact_tags=spec.contact,
                    # The verified frame for every class shows both men on their feet, and the
                    # contact sheet records no other posture, so only standing is claimed.
                    postures=(Posture.standing,),
                    setting=SETTING,
                    camera_angles=CAMERA_ANGLES,
                    frame_count=entry["nb_frames"],
                    native_fps=entry["fps"],
                    duration_s=entry["duration_s"],
                    width=entry["width"],
                    height=entry["height"],
                    pose_format="bbox_only" if len(files) == 2 else "none",
                    pose_root=LABELS_XLS if len(files) == 2 else None,
                    caption=_segmented_caption(entry, label, start, end, actor),
                    caption_source="parsed_index",
                    measured=measured,
                    files=files,
                    ingested_at=ingested_at,
                    ingester_version=INGESTER_VERSION,
                )
            )
    return out


def _sequence_clips(
    root: Path,
    index: dict[str, Any],
    classes: dict[int, _Class],
    primary: dict[int, list[Any]],
    others: dict[int, int],
    xls: ReferenceFile | None,
    ingested_at: str,
    skipped: list[str],
) -> list[ReferenceClip]:
    """One clip per uncut sequence AVI, tagged with the union of the actions annotated in it."""
    out: list[ReferenceClip] = []
    for entry in sorted(index["ut_sequences.json"], key=lambda e: e["seq"]):
        match = _SEQUENCE_RE.match(entry["seq"])
        if match is None:
            skipped.append(f"ut {entry['seq']}: not a seq<N> name, so it is not ingested")
            continue
        sequence = int(match.group(1))
        annotated: list[tuple[dict[str, Any], _Class]] = []
        for row in primary.get(sequence, []):
            class_id = _as_int(row.get("Activity Class ID"))
            known = classes.get(class_id) if class_id is not None else None
            if known is not None:
                annotated.append((row, known))
        if not annotated:
            skipped.append(
                f"ut {entry['seq']}: no usable annotation rows, so the uncut sequence is dropped"
            )
            continue
        video = _reference_file(root, entry["file"], "video")
        if video is None:
            skipped.append(f"ut {entry['seq']}: {entry['file']} is not on disk")
            continue
        picked = [known for _, known in annotated]
        tags = tuple(sorted({known.spec.tag for known in picked}, key=lambda tag: tag.value))
        # 'none' cannot sit beside a real contact, and pointing is only one of the six actions in
        # every take, so the sequence keeps the contacts it really contains.
        contact = tuple(
            sorted(
                {tag for known in picked for tag in known.spec.contact} - {ContactTag.none},
                key=lambda tag: tag.value,
            )
        )
        # Any take with a punch, a kick and a push in it is aggression, whatever else it holds.
        affection = (
            Affection.aggression
            if any(known.spec.affection is Affection.aggression for known in picked)
            else Affection.affection
        )
        files = (video, xls) if xls is not None else (video,)
        labels = [known.label for known in picked]
        out.append(
            ReferenceClip(
                clip_id=f"ut_{entry['seq']}_uncut",
                source=SOURCE,
                source_ref=entry["file"].removeprefix("UT-Interaction/raw/"),
                modality=Modality.video_rgb,
                usage=USAGE,
                # Only the acting pair is annotated. Sequences with 'others:' rows have further
                # people in shot whom the spreadsheet never counts, so 2 is what is known.
                people_count=2,
                affection=affection,
                interaction_tags=tags,
                contact_tags=contact,
                # The annotated actions are separated by unannotated gaps at changing coordinates,
                # which is the pair walking into position: that gap is why the uncut take is worth
                # keeping at all.
                postures=(Posture.standing, Posture.walking),
                setting=SETTING,
                camera_angles=CAMERA_ANGLES,
                frame_count=entry["nb_frames"],
                native_fps=entry["fps"],
                duration_s=entry["duration_s"],
                width=entry["width"],
                height=entry["height"],
                pose_format="bbox_only" if xls is not None else "none",
                pose_root=LABELS_XLS if xls is not None else None,
                caption=_sequence_caption(entry, labels, others.get(sequence, 0)),
                caption_source="parsed_index",
                measured={
                    "annotated_actions": float(len(annotated)),
                    "other_annotations": float(others.get(sequence, 0)),
                    "annotated_start_frames": [
                        float(_as_int(row["Starting Frame #"]) or 0) for row, _ in annotated
                    ],
                },
                files=files,
                ingested_at=ingested_at,
                ingester_version=INGESTER_VERSION,
            )
        )
    return out


def _segmented_caption(
    entry: dict[str, Any], label: str, start: int | None, end: int | None, actor: int | None
) -> str:
    """A sentence that says what the clip is and where in the uncut take it came from."""
    where = f"UT-Interaction {entry['set'].replace('_', ' ')}, sequence {entry['field_b']}"
    what = f"two men {label.replace('_', ' ')} on a concrete parking lot in bright daylight"
    how = "fixed high-angle camera, static wide shot"
    if start is None or end is None:
        return f"{where}: {what}, {how}. Segmented excerpt with no spreadsheet row of its own."
    who = _ACTOR_WORDS.get(actor if actor is not None else -1, "both")
    return (
        f"{where}: {what}, {how}. Segmented excerpt of frames {start} to {end} of"
        f" seq{entry['field_b']}.avi, acted by {who}."
    )


def _sequence_caption(entry: dict[str, Any], labels: list[str], others: int) -> str:
    """A sentence that says the clip is the uncut take and what it contains."""
    listed = ", ".join(label.replace("_", " ") for label in labels)
    tail = (
        f" The spreadsheet marks {others} further action instances in it under 'others:'."
        if others
        else ""
    )
    return (
        f"UT-Interaction {entry['seq']}.avi, the uncut {entry['duration_s']:.0f} second take the"
        f" segmented clips are cut from: {len(labels)} annotated actions in order ({listed}), two"
        f" men on a concrete parking lot in bright daylight, fixed high-angle camera.{tail}"
    )
