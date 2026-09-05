"""CMU Graphics Lab mocap, the source that actually drives a shot.

Two kinds of clip come out of here and they are not the same material.

The baked clips in ``/mnt/fast/models/blender-assets/clips`` come first: 55 two-person takes plus
the solo trials the recipe asks for by name, all retargeted to ``cf.clip.v2`` world segment
directions and already tagged in this contract's vocabulary by the baker. A ShotSpec can name one
of them directly, so these are the clips retrieval exists to find. The solo ones exist because the
two-person set holds no sustained run - its fastest "running" clip is a two-metre scramble for a
chair - so a beat that needs somebody actually running has to reach a single-subject sprint trial.

The rest are the single-person trials: one skeleton, one AMC of joint angles, and CMU's own
one-line description. They carry no interaction, and they say so with ``no_contact`` rather than
with a guess. They are here because they are the largest pile of real human motion on disk and
because a query for a posture should be able to reach them.

Two honesty rules shaped this module. Frame counts are parsed, never estimated from file size: an
AMC numbers its frame blocks from 1 with no gaps, which was checked against a full line count for
all 2514 files on disk, so the last number in the file is the count and the tail is enough to read
it. And a trial CMU never indexed has no frame rate anywhere, not in the AMC and not on the index
page, so it is skipped by name instead of being handed an invented 120.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path

from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceSource,
    UsageClass,
)

INGESTER_VERSION = "0.1.0"
SOURCE = ReferenceSource.cmu_mocap

TRIALS_JSON = "_index/measured/cmu.json"
"""Already measured: 2514 trials with description, category, framerate and paths."""

CLASS_MAPS_JSON = "_index/measured/class_maps.json"
"""Verified by eye. Read here only for the list of two-person subjects."""

CLIP_LIBRARY = "../models/blender-assets/clips"
"""The baked clips sit beside the weights, not under the reference root, because they are output
of this repo rather than downloaded data. Every ``ReferenceFile.path`` is relative to ``root``, so
the relative form has to climb out of it; ``root / path`` still resolves to the file."""

CLIP_MANIFEST = "manifest.json"

SETTING = "studio"
"""Every CMU trial is the same Vicon volume however the description reads. A trial described as a
playground was pantomimed on a mocap stage, and a query about a place must not match it."""

_POSTURE_WORDS: tuple[tuple[Posture, tuple[str, ...]], ...] = (
    (
        Posture.walking,
        (
            "walk",
            "walks",
            "walking",
            "stroll",
            "strolls",
            "strolling",
            "pace",
            "paces",
            "pacing",
            "march",
            "marches",
            "marching",
            "step",
            "steps",
            "stepping",
            "tiptoe",
            "tiptoes",
            "tiptoeing",
            "wander",
            "wanders",
            "wandering",
        ),
    ),
    (
        Posture.running,
        (
            "run",
            "runs",
            "running",
            "jog",
            "jogs",
            "jogging",
            "sprint",
            "sprints",
            "sprinting",
            "dash",
            "dashes",
            "dashing",
        ),
    ),
    (Posture.sitting, ("sit", "sits", "sitting", "seated")),
    (
        Posture.dancing,
        (
            "dance",
            "dances",
            "dancing",
            "danced",
            "salsa",
            "waltz",
            "tango",
            "samba",
            "rumba",
            "charleston",
            "charelston",
            "ballet",
            "jig",
        ),
    ),
    (Posture.kneeling, ("kneel", "kneels", "kneeling")),
    (Posture.crouching, ("crouch", "crouches", "crouching", "squat", "squats", "squatting")),
    (Posture.lying, ("lie", "lies", "lying", "lay", "laying", "supine", "prone")),
    (Posture.standing, ("stand", "stands", "standing")),
    (Posture.leaning, ("lean", "leans", "leaning")),
)
"""Whole words, spelled out. A stemmer would match "sit" inside "visit" and "lean" inside "clean",
and CMU's descriptions are short enough that the list is cheaper to audit than a rule. The
misspelling "charelston" is CMU's own."""

_OFF_FEET_WORDS: tuple[str, ...] = (
    "climb",
    "climbs",
    "climbing",
    "hang",
    "hangs",
    "hanging",
    "dangle",
    "dangles",
    "dangling",
    "swim",
    "swims",
    "swimming",
    "crawl",
    "crawls",
    "crawling",
    "roll",
    "rolls",
    "rolling",
    "somersault",
    "somersaults",
    "cartwheel",
    "cartwheels",
    "handstand",
    "handstands",
    "backflip",
    "backflips",
    "acrobatics",
)
"""Postures the closed vocabulary has no word for. A trial that names one of these and nothing
else is skipped: ``Posture`` can say standing, sitting, kneeling, walking, running, crouching,
lying, dancing and leaning, and none of them is true of a body on a ladder. Reporting the gap is
the point, the same way ``ABSENT_INTERACTIONS`` reports the interactions nothing on disk covers."""

_POSTURE_PATTERNS: tuple[tuple[Posture, re.Pattern[str]], ...] = tuple(
    (posture, re.compile(r"\b(?:" + "|".join(words) + r")\b")) for posture, words in _POSTURE_WORDS
)

_OFF_FEET_PATTERN = re.compile(r"\b(?:" + "|".join(_OFF_FEET_WORDS) + r")\b")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _words(text: str) -> str:
    """One CMU description, lowercased, with runs like "NormalWalk" split into words.

    212 trials are named in camel case, so without the split "SlowWalk" names no posture at all.
    This is a spelling normalisation and nothing more: no word is added or renamed.
    """
    return _CAMEL_BOUNDARY.sub(" ", text).lower()


FALLBACK_POSTURE = Posture.standing
"""``postures`` cannot be empty, and a trial whose text names no posture still had a body in the
volume. Standing is the conservative reading: CMU starts every trial from a standing calibration
pose, and the trials that leave the feet - sit, kneel, lie - say so in their text. What the trial
actually shows stays in the caption, which is what retrieval scores."""


def postures_from_text(text: str) -> tuple[Posture, ...]:
    """The postures named in one CMU description, sorted and unique.

    Guarantees: only whole-word matches from ``_POSTURE_WORDS`` count, the result is sorted by
    enum value so it satisfies ``ReferenceClip``, and it is never empty - text that names no
    posture yields ``FALLBACK_POSTURE``.
    """
    lowered = _words(text)
    found = {posture for posture, pattern in _POSTURE_PATTERNS if pattern.search(lowered)}
    if not found:
        return (FALLBACK_POSTURE,)
    return tuple(sorted(found, key=lambda p: p.value))


def posture_outside_vocabulary(text: str) -> bool:
    """True when the text names a posture ``Posture`` cannot express: climbing, hanging, swimming,
    crawling, tumbling.

    Guarantee: only used to skip a trial, never to tag one. A caller that gets True has a trial
    whose only honest posture is a word the contract does not have.
    """
    return bool(_OFF_FEET_PATTERN.search(_words(text)))


def amc_frame_count(path: Path, *, window: int = 8192) -> int | None:
    """The exact number of frames in an AMC file, or None when it holds no frame block.

    Guarantee: this is a parse, not an estimate. AMC numbers its frame blocks from 1 with no gaps,
    so the last bare integer in the file is the frame count; that was verified against a full line
    count for every one of the 2514 trials on disk, and reading the tail instead of the whole file
    is the difference between 0.3 s and 11 s over the corpus.
    """
    size = path.stat().st_size
    read = window
    while True:
        start = max(0, size - read)
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read()
        lines = chunk.split(b"\n")
        if start > 0:
            # The first line is a fragment, and the tail of a joint line can be all digits.
            del lines[0]
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.isdigit():
                return int(stripped)
        if start == 0:
            return None
        read *= 4


def _digest(path: Path, cache: dict[Path, tuple[str, int]]) -> tuple[str, int]:
    """sha256 and size of one file, computed once per path.

    The cache matters: 112 ASF skeletons are shared by 2514 trials, so without it the same
    skeleton would be hashed twenty times.
    """
    hit = cache.get(path)
    if hit is not None:
        return hit
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    result = (digest.hexdigest(), path.stat().st_size)
    cache[path] = result
    return result


def _load_json(path: Path) -> object | None:
    """Parsed JSON, or None when the file is missing or unreadable."""
    try:
        with path.open("rb") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _two_person_subjects(root: Path, skipped: list[str]) -> frozenset[str]:
    """The CMU subjects recorded as A/B pairs, from the verified class map.

    Guarantee: a subject in this set never becomes a single-person clip. Half of a two-person take
    is not a one-person trial, so if no baked clip covers it the trial is skipped rather than
    described as somebody alone.
    """
    data = _load_json(root / CLASS_MAPS_JSON)
    pairs = None
    if isinstance(data, dict):
        block = data.get("cmu_two_person_subjects")
        if isinstance(block, dict):
            pairs = block.get("pairs")
    if not isinstance(pairs, list):
        skipped.append(
            f"cmu: {CLASS_MAPS_JSON} has no cmu_two_person_subjects.pairs, so a two-person trial"
            " with no baked clip cannot be told apart from a solo trial"
        )
        return frozenset()
    return frozenset(str(subject) for pair in pairs if isinstance(pair, list) for subject in pair)


def _tag[T: StrEnum](value: object, kind: type[T]) -> T | None:
    """One vocabulary value, or None when the string is not in the closed vocabulary."""
    try:
        return kind(value)
    except ValueError:
        return None


def _measured(measured: dict[str, object]) -> dict[str, float | list[float]]:
    """The baker's numbers, flattened to the floats and float lists the contract allows.

    ``travel_m`` and ``yaw_range_deg`` are per actor in the manifest, so they are split into an
    ``_a`` and a ``_b`` key rather than averaged: which actor walked two metres is the point.
    """
    out: dict[str, float | list[float]] = {}
    for key in ("closest_wrists_m", "ground_offset"):
        value = measured.get(key)
        if isinstance(value, (int, float)):
            out[key] = float(value)
    gap = measured.get("root_gap_m")
    if isinstance(gap, list) and all(isinstance(v, (int, float)) for v in gap):
        out["root_gap_m"] = [float(v) for v in gap]
    for key in ("travel_m", "yaw_range_deg"):
        per_actor = measured.get(key)
        if not isinstance(per_actor, dict):
            continue
        for actor in ("a", "b"):
            value = per_actor.get(actor)
            if isinstance(value, (int, float)):
                out[f"{key}_{actor}"] = float(value)
            elif isinstance(value, list) and all(isinstance(v, (int, float)) for v in value):
                out[f"{key}_{actor}"] = [float(v) for v in value]
    # speed_mps is per actor AND nested ({actor: {peak, sustained_1s}}), so it needs its own pass.
    # It is worth carrying: the posture tag says "running", this says how fast, which is the
    # difference between a sprint and a hurried walk.
    speeds = measured.get("speed_mps")
    if isinstance(speeds, dict):
        for actor in ("a", "b"):
            per_actor = speeds.get(actor)
            if not isinstance(per_actor, dict):
                continue
            for metric in ("peak", "sustained_1s"):
                value = per_actor.get(metric)
                if isinstance(value, (int, float)):
                    out[f"speed_{metric}_{actor}"] = float(value)
    return out


def _baked_clips(
    root: Path,
    *,
    ingested_at: str,
    skipped: list[str],
    cache: dict[Path, tuple[str, int]],
) -> tuple[list[ReferenceClip], set[tuple[str, str]]]:
    """The baked clips, plus the (subject, trial) pairs they cover.

    Most are two-person takes; an entry naming one subject is a solo clip (the running trials),
    which is why ``cast`` is a list rather than a pair. A trial covered here is never also
    ingested as a raw AMC by ``_solo``: the baked clip is the stageable form of it.
    """
    library = root / CLIP_LIBRARY
    manifest = _load_json(library / CLIP_MANIFEST)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("clips"), list):
        skipped.append(
            f"cmu: {CLIP_LIBRARY}/{CLIP_MANIFEST} is absent or unreadable, so none of the baked"
            " two-person clips was ingested"
        )
        return [], set()

    library_fps = manifest.get("fps")
    clips: list[ReferenceClip] = []
    covered: set[tuple[str, str]] = set()
    entries = [c for c in manifest["clips"] if isinstance(c, dict)]
    for entry in sorted(entries, key=lambda c: str(c.get("name", ""))):
        name = str(entry.get("name", ""))
        subjects = entry.get("subjects")
        trial = str(entry.get("trial", ""))
        if not isinstance(subjects, list) or len(subjects) not in (1, 2) or not trial:
            skipped.append(
                f"cmu clip {name}: the manifest entry names no subject (or subject pair) and trial"
            )
            continue
        cast = [str(v) for v in subjects]
        for subject in cast:
            covered.add((subject, trial))

        rel = f"{CLIP_LIBRARY}/{name}.json"
        path = library / f"{name}.json"
        if not path.is_file():
            skipped.append(f"cmu clip {name}: {rel} is not on disk")
            continue

        measured = entry.get("measured")
        measured = measured if isinstance(measured, dict) else {}
        frames = measured.get("frames")
        duration = measured.get("duration_s")
        fps = library_fps if isinstance(library_fps, (int, float)) else None
        if not isinstance(frames, int) or not isinstance(duration, (int, float)) or not fps:
            skipped.append(
                f"cmu clip {name}: the manifest gives no measured frames, duration and fps, and a"
                " clip may not claim numbers it does not have"
            )
            continue

        interaction = _tag(entry.get("interaction"), InteractionTag)
        affection_value = entry.get("affection")
        if interaction is None:
            skipped.append(
                f"cmu clip {name}: interaction {entry.get('interaction')!r} is not in the"
                " contract vocabulary"
            )
            continue
        try:
            affection = Affection(affection_value)
        except ValueError:
            skipped.append(
                f"cmu clip {name}: affection {affection_value!r} is not in the contract vocabulary"
            )
            continue

        raw_contact = entry.get("contact")
        contacts = sorted({str(v) for v in raw_contact} if isinstance(raw_contact, list) else set())
        unknown_contact = [v for v in contacts if _tag(v, ContactTag) is None]
        raw_posture = entry.get("posture")
        postures = sorted({str(v) for v in raw_posture} if isinstance(raw_posture, list) else set())
        unknown_posture = [v for v in postures if _tag(v, Posture) is None]
        if unknown_contact or unknown_posture:
            skipped.append(
                f"cmu clip {name}: contact {unknown_contact} posture {unknown_posture} are not in"
                " the contract vocabulary"
            )
            continue
        if not postures:
            skipped.append(f"cmu clip {name}: the manifest names no posture")
            continue

        sha256, size_bytes = _digest(path, cache)
        description = str(entry.get("description", ""))
        people = entry.get("people")
        clips.append(
            ReferenceClip(
                clip_id=name,
                source=SOURCE,
                source_ref="+".join(f"subjects/{s}/{s}_{trial}.amc" for s in cast),
                modality=Modality.mocap_segments,
                # pose_derivable, not pixels_usable: a cf.clip.v2 holds world segment directions
                # and no image at all, so there are no pixels to look at, only a rig to drive.
                usage=UsageClass.pose_derivable,
                people_count=people if isinstance(people, int) else len(cast),
                affection=affection,
                interaction_tags=(interaction,),
                contact_tags=tuple(ContactTag(v) for v in contacts),
                postures=tuple(Posture(v) for v in postures),
                setting=SETTING,
                frame_count=frames,
                native_fps=float(fps),
                duration_s=float(duration),
                pose_format="cf_clip_v2",
                pose_root=rel,
                retargeted_clip=name,
                # CMU's own index text, minus the "(2 subjects - subject A)" suffix the baker
                # stripped. Parsed from the index pages, not shipped inside the dataset.
                caption=description,
                caption_source="parsed_index" if description else "derived",
                measured=_measured(measured),
                files=(
                    ReferenceFile(role="clip_json", path=rel, sha256=sha256, size_bytes=size_bytes),
                ),
                ingested_at=ingested_at,
                ingester_version=INGESTER_VERSION,
            )
        )
    return clips, covered


def _trial_clips(
    root: Path,
    *,
    ingested_at: str,
    skipped: list[str],
    cache: dict[Path, tuple[str, int]],
    covered: set[tuple[str, str]],
    two_person: frozenset[str],
) -> list[ReferenceClip]:
    """One clip per single-person AMC trial that no baked clip already covers."""
    trials = _load_json(root / TRIALS_JSON)
    if not isinstance(trials, list):
        skipped.append(
            f"cmu: {TRIALS_JSON} is absent or unreadable, so no single-person trial was ingested"
        )
        return []

    clips: list[ReferenceClip] = []
    rows = [t for t in trials if isinstance(t, dict)]
    for row in sorted(rows, key=lambda t: (str(t.get("subject")), str(t.get("trial")))):
        subject = str(row.get("subject", ""))
        trial = str(row.get("trial", ""))
        key = (subject, trial)
        if key in covered:
            continue
        if subject in two_person:
            skipped.append(
                f"cmu {subject}_{trial}: subject {subject} is half of an A/B pair and no baked"
                " clip covers this trial, so there is no honest people_count for it"
            )
            continue

        framerate = row.get("framerate")
        if not isinstance(framerate, (int, float)) or framerate <= 0:
            skipped.append(
                f"cmu {subject}_{trial}: CMU never indexed this trial, so there is no frame rate"
                " for it and the AMC does not carry one"
            )
            continue

        amc_rel = str(row.get("amc", ""))
        asf_rel = str(row.get("asf", ""))
        amc_path = root / amc_rel
        asf_path = root / asf_rel
        if not amc_rel or not amc_path.is_file():
            skipped.append(f"cmu {subject}_{trial}: {amc_rel or 'its AMC'} is not on disk")
            continue
        if not asf_rel or not asf_path.is_file():
            skipped.append(
                f"cmu {subject}_{trial}: skeleton {asf_rel or 'ASF'} is not on disk, and AMC"
                " joint angles mean nothing without it"
            )
            continue

        frames = amc_frame_count(amc_path)
        if frames is None or frames < 1:
            skipped.append(f"cmu {subject}_{trial}: {amc_rel} holds no numbered frame block")
            continue

        description = str(row.get("description", "")).strip()
        category = str(row.get("category", "")).strip()
        # An empty description cell on CMU's index page, 90 of them, falls back to the subject's
        # category. That is us choosing the caption, so the source is "derived", not the dataset.
        caption = description or category
        caption_source = "parsed_index" if description else "derived"

        postures = postures_from_text(caption)
        if postures == (FALLBACK_POSTURE,) and posture_outside_vocabulary(caption):
            skipped.append(
                f"cmu {subject}_{trial}: {caption!r} names no posture the vocabulary can express"
                " (climbing, hanging, swimming, crawling, tumbling), and calling it standing"
                " would be a lie"
            )
            continue

        amc_sha, amc_size = _digest(amc_path, cache)
        asf_sha, asf_size = _digest(asf_path, cache)
        files = [
            ReferenceFile(role="skeleton", path=amc_rel, sha256=amc_sha, size_bytes=amc_size),
            ReferenceFile(role="skeleton_def", path=asf_rel, sha256=asf_sha, size_bytes=asf_size),
        ]
        avi_rel = row.get("avi")
        if isinstance(avi_rel, str) and avi_rel and (root / avi_rel).is_file():
            # A rendered stick figure on black. Listed so a human can eyeball the motion without
            # importing it, and deliberately not what "pixels_usable" would mean.
            avi_sha, avi_size = _digest(root / avi_rel, cache)
            files.append(
                ReferenceFile(role="video", path=avi_rel, sha256=avi_sha, size_bytes=avi_size)
            )

        clips.append(
            ReferenceClip(
                clip_id=f"cmu_{subject}_{trial}",
                source=SOURCE,
                source_ref=f"subjects/{subject}/{subject}_{trial}.amc",
                modality=Modality.mocap_skeleton,
                # pose_derivable: the ASF plus AMC is a real skeleton, and the only images are the
                # stick-figure AVIs, which are not a visual reference for anything.
                usage=UsageClass.pose_derivable,
                people_count=1,
                # One body alone. Affection describes an interaction, and with nobody to interact
                # with the measured answer is neutral rather than a reading of the description.
                affection=Affection.neutral,
                # No second person, so no interaction, which the contract spells "no_contact" and
                # allows to stand with no contact tag at all. A vague description like "greeting"
                # does not earn a handshake tag here.
                interaction_tags=(InteractionTag.no_contact,),
                contact_tags=(),
                postures=postures,
                setting=SETTING,
                frame_count=frames,
                native_fps=float(framerate),
                duration_s=frames / float(framerate),
                pose_format="asf_amc",
                pose_root=str(Path(amc_rel).parent),
                caption=caption,
                caption_source=caption_source,
                files=tuple(files),
                ingested_at=ingested_at,
                ingester_version=INGESTER_VERSION,
            )
        )
    return clips


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every clip CMU contributes, plus one string per thing skipped and why.

    Guarantees: clips are sorted by ``clip_id`` and every ``clip_id`` is unique; every
    ``ReferenceFile.path`` is relative to ``root`` and its ``sha256`` is the digest of the bytes on
    disk; nothing is read from the clock, so the same bytes and the same ``ingested_at`` produce
    byte-identical output; a two-person take appears once, as the baked clip, and never also as
    two solo trials.
    """
    skipped: list[str] = []
    cache: dict[Path, tuple[str, int]] = {}
    two_person = _two_person_subjects(root, skipped)
    baked, covered = _baked_clips(root, ingested_at=ingested_at, skipped=skipped, cache=cache)
    trials = _trial_clips(
        root,
        ingested_at=ingested_at,
        skipped=skipped,
        cache=cache,
        covered=covered,
        two_person=two_person,
    )
    clips = sorted([*baked, *trials], key=lambda c: c.clip_id)
    return clips, skipped
