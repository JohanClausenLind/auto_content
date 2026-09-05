"""MotionHub: SMPL-H motion with hierarchical text captions, one body at a time.

Three subsets of ``ZeyuLing/MotionHub`` are on disk under ``MotionHub/raw``: EgoBody and GRAB are
complete, HumanML3D_AMASS is a partial download. None of them is two-person interaction data, which
is worth stating plainly because the repository's own name suggests otherwise.

**Why every clip here declares ``people_count`` 1.** EgoBody is a two-person recording, so a pair of
``body_idx_0`` / ``body_idx_1`` files looks like one two-person clip. It is not. MotionHub's
conversion re-origins each body independently: in ``recording_20210929_S15_S11_03/000`` both bodies
start at exactly ``transl`` x=0, z=0, giving a frame-0 horizontal separation of 0.000 m, which no
real pair of adults can occupy. Their tracks then wander apart to 2.208 m in segment 000 and stay
within 0.416 m for all 501 frames of segment 001, and each body carries its own
``conversion_y_shift`` (-0.1527 against -0.1435 for that recording), so the two are not even in a
common vertical frame. The pairing survives in ``source_ref``, which keeps the recording name and
the body index, but the geometry between two bodies is not recoverable and must not be indexed as
contact.

**Why the captions are the payload.** These are single-body motions, so the closed interaction
vocabulary has almost nothing true to say about them. The hierarchical captions do: an action
phrase plus macro, meso and micro sentences written for the motion. They go in ``caption`` with
``caption_source`` "dataset", and that is what makes these clips findable at all.

**Postures come from the caption text, with the geometry as a fallback and a cross-check.** The
obvious route is the root height in ``transl``, and it separates the extremes in HumanML3D_AMASS,
where motions whose text says "lie" read a median root height of 0.61 m, and a median per-clip
minimum of 0.40 m, against 1.13 m for the ones that say "stand". The gap is smaller than it looks:
a motion captioned "lie down" starts upright, so the median over its frames sits between the two
postures rather than at the floor. Height alone does
not work for EgoBody: over its 963 captioned motions, the ones whose text says "sit" read a median
1.148 m against 1.176 m for the ones that say "stand", a 3 cm gap, so height would call a seated
person standing. The captions say it outright, so posture is read from their word tokens, which
covers 79 % of EgoBody and 84 % of HumanML3D_AMASS. The fallback is the measured root height and
horizontal path speed, and it carries almost all of GRAB, whose captions describe the object being
picked up and never the posture. Either way the numbers behind the decision go into ``measured``, so
a clip whose posture disagrees with its geometry can be found later.
"""

from __future__ import annotations

import json
import re
import string
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from content_factory.schemas.base import file_sha256
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

SOURCE = ReferenceSource.motionhub_egobody
"""The build driver's single-source handle. This one module covers three ``ReferenceSource`` values
because the three subsets share one directory layout, one file format and one caption schema; the
full list is ``SOURCES`` and every clip carries its own subset's source."""

SOURCES: tuple[ReferenceSource, ...] = (
    ReferenceSource.motionhub_egobody,
    ReferenceSource.motionhub_grab,
    ReferenceSource.motionhub_humanml3d,
)

MOTIONHUB_ROOT = "MotionHub/raw"

HUMANML3D_REMOTE_FILES = 43765
"""What the remote repository lists for the HumanML3D_AMASS subset, from INVENTORY.json. Quoted in
the skipped lines so a partial download can never be mistaken for a finished one.

Read it as a provenance note, not as the size of the gap: the subset on disk now holds exactly
43765 files itself (11197 ``.npz`` plus 32568 ``.json``), so the two numbers matching proves
nothing either way. The incompleteness is measured from the tree instead, and it is not subtle.
The caption directories 0112 to 0146 have no ``smplh_52`` directory at all, and 0111 holds 91
motions against 112 captions, i.e. the fetch stopped inside a directory. Two thirds of the gap is
the ``M000`` to ``M014`` caption directories, 16116 of the 22105 absences: in HumanML3D an ``M``
prefix marks a mirrored motion, so those may never have had their own ``.npz`` upstream, but a
fetch that sorts names would also not have reached them yet. Which of the two it is cannot be told
from this tree, and it does not change what the ingester does with them."""

_STANDING_MIN_M = 0.85
_LYING_MAX_M = 0.55
_WALK_MIN_MPS = 0.35
_RUN_MIN_MPS = 1.40
"""Thresholds for the fallback, on the measured root height and horizontal path speed.

Measured over the clips this ingester emits, by caption word: "sit on the floor" 0.54 m, "lie down"
0.51 m (0.39 m at its lowest frame), "sit" on a chair or sofa 0.93 m, "stand" 1.02 to 1.16 m across
the tenth and ninetieth percentile with a median of 1.13 m, and GRAB a flat 1.15 m; walk captions
read a median 0.55 m/s and run captions 0.95 m/s.

So these cuts sit below the lying and chair-sitting medians and the run median, and the fallback
under-calls all three rather than over-calling them, which is the safer direction: it answers
"standing" where it is unsure instead of inventing a posture. It is also a small population. The
fallback decides 3151 of 12761 clips, almost all of them GRAB, and only 52 of those read below
0.85 m and 2 below 0.55 m, so nine in ten of them are plainly upright. Every number behind the
decision is on the clip in ``measured``, so a wrong call is findable rather than hidden."""

_WORD = re.compile(r"[a-z]+")

_POSTURE_TOKENS: tuple[tuple[Posture, frozenset[str]], ...] = (
    (Posture.lying, frozenset({"lie", "lies", "lying", "lay", "prone", "supine"})),
    (Posture.sitting, frozenset({"sit", "sits", "sitting", "seated"})),
    (Posture.kneeling, frozenset({"kneel", "kneels", "kneeling"})),
    (
        Posture.crouching,
        frozenset({"crouch", "crouches", "crouching", "squat", "squats", "squatting"}),
    ),
    (Posture.leaning, frozenset({"lean", "leans", "leaning"})),
    (Posture.dancing, frozenset({"dance", "dances", "dancing"})),
    (
        Posture.running,
        frozenset({"run", "runs", "running", "jog", "jogs", "jogging", "sprint", "sprints"}),
    ),
    (Posture.walking, frozenset({"walk", "walks", "walking", "stroll", "strolls", "strolling"})),
    (Posture.standing, frozenset({"stand", "stands", "standing", "upright"})),
)
"""Whole word tokens, never substrings: "transition" contains "sit" and "outstanding" contains
"stand", and both appear in these captions."""

_NAMED_ABSENCE_LIMIT = 8
"""Above this many missing files in one directory the skipped line gives both counts instead of
every name. HumanML3D_AMASS is short 22105 caption-only entries, and a manifest carrying 22105
strings would be unreadable, which is the opposite of the point."""

_ID_CHARS = frozenset(string.ascii_lowercase + string.digits + "_")

_MAX_CLIP_ID = 64
_MAX_SOURCE_REF = 200
_MAX_CAPTION = 600

_AGGRESSION_CATEGORY = "martial arts / combat"
"""The one caption category that is not neutral.

The captions on disk carry thirteen category values: daily activity (15495), locomotion (12309),
exercise / workout (1981), sports (1595), martial arts / combat (1094), dance (1073), speech
gesture (456), physical interaction (262), professional / work activity (257), stunt / acrobatic
(212), other (130), and one caption each of "unknown" and "acrobatic". Anything not matched here
becomes neutral, so a fourteenth value costs a wrong tag on nothing.

"physical interaction" is the tempting second candidate for aggression and is left neutral: it is
almost all "get pushed, stumble backwards", a single body reacting to a partner who is not in the
file, so it is neither a shove given nor a scene to keep out of a tender one. No MotionHub clip is
affection either: a single body cannot be tender towards anybody."""


@dataclass(frozen=True)
class _Subset:
    """One MotionHub subset: where it lives and what is true of every clip in it."""

    directory: str
    source: ReferenceSource
    id_prefix: str
    dir_depth: int
    setting: str
    contact_tags: tuple[ContactTag, ...]
    complete: bool
    id_replacements: tuple[tuple[str, str], ...] = ()
    remote_files: int | None = None


SUBSETS: tuple[_Subset, ...] = (
    _Subset(
        directory="EgoBody",
        source=ReferenceSource.motionhub_egobody,
        id_prefix="mh_ego",
        # smplh_52/<recording>/<body_idx_N>/<segment>.npz
        dir_depth=2,
        setting="indoor room, one body of a pair",
        # No contact is recoverable: the partner body is in its own world frame, see the docstring.
        contact_tags=(),
        complete=True,
        id_replacements=(("recording_", ""), ("body_idx_", "b")),
    ),
    _Subset(
        directory="GRAB",
        source=ReferenceSource.motionhub_grab,
        id_prefix="mh_grab",
        # smplh_52/<subject>/<take>.npz
        dir_depth=1,
        setting="mocap lab, tabletop objects",
        # Every GRAB take is one person grasping an object, so hand-to-object contact is a fact of
        # the subset even though there is no person-to-person contact in it.
        contact_tags=(ContactTag.object,),
        complete=True,
    ),
    _Subset(
        directory="HumanML3D_AMASS",
        source=ReferenceSource.motionhub_humanml3d,
        id_prefix="mh_hml",
        dir_depth=1,
        setting="unknown",
        contact_tags=(),
        complete=False,
        remote_files=HUMANML3D_REMOTE_FILES,
    ),
)

_SUBSET_LABELS = {
    "EgoBody": "egobody",
    "GRAB": "grab",
    "HumanML3D_AMASS": "humanml3d",
}


@dataclass(frozen=True)
class _Motion:
    """The measured part of one ``.npz``, with nothing kept that is not used."""

    frame_count: int
    native_fps: float
    root_height_median_m: float
    root_height_min_m: float
    root_height_max_m: float
    travel_m: float
    speed_mps: float


@dataclass(frozen=True)
class _Caption:
    text: str
    category: str


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every MotionHub motion that has both its ``.npz`` and its caption, plus what was left out.

    Guarantees: clips are sorted by ``clip_id`` and the skipped lines are sorted, so two runs over
    an unchanged tree return identical bytes; every ``ReferenceFile.path`` is relative to ``root``
    and its ``sha256`` is the real digest of the file; a subset whose files are missing contributes
    skipped lines naming the gap rather than clips; and an absent ``MotionHub/raw`` yields no clips
    and one skipped line rather than an exception.
    """
    base = root / MOTIONHUB_ROOT
    if not base.is_dir():
        return [], [f"motionhub: {MOTIONHUB_ROOT} is not on disk, no MotionHub clip was ingested"]

    clips: list[ReferenceClip] = []
    skipped: list[str] = []
    seen: dict[str, str] = {}

    for subset in SUBSETS:
        subset_clips, subset_skipped = _ingest_subset(root, base, subset, ingested_at, seen)
        clips.extend(subset_clips)
        skipped.extend(subset_skipped)

    clips.sort(key=lambda clip: clip.clip_id)
    return clips, sorted(skipped)


def _ingest_subset(
    root: Path,
    base: Path,
    subset: _Subset,
    ingested_at: str,
    seen: dict[str, str],
) -> tuple[list[ReferenceClip], list[str]]:
    """One subset's clips and skipped lines. ``seen`` maps an already-used clip id to its stem."""
    label = _SUBSET_LABELS[subset.directory]
    motion_dir = base / subset.directory / "smplh_52"
    caption_dir = base / subset.directory / "hierarchical_caption"
    if not motion_dir.is_dir() and not caption_dir.is_dir():
        return [], [f"motionhub {label}: subset directory is not on disk"]

    motions = _walk_stems(motion_dir, subset.dir_depth, ".npz")
    captions = _walk_stems(caption_dir, subset.dir_depth, ".json")
    motion_set, caption_set = set(motions), set(captions)

    clips: list[ReferenceClip] = []
    skipped: list[str] = []

    for stem in sorted(motion_set & caption_set):
        clip, reason = _build_clip(root, base, subset, stem, ingested_at, seen)
        if clip is not None:
            clips.append(clip)
        if reason is not None:
            skipped.append(reason)

    skipped.extend(
        _absence_lines(
            label=label,
            missing=sorted(motion_set - caption_set),
            total=len(motions),
            noun="motions",
            reason="no hierarchical_caption .json, and the caption is the only searchable text",
        )
    )
    skipped.extend(
        _absence_lines(
            label=label,
            missing=sorted(caption_set - motion_set),
            total=len(captions),
            noun="captioned motions",
            reason="no smplh_52 .npz on disk",
        )
    )

    if not subset.complete:
        remote = subset.remote_files or 0
        skipped.append(
            f"motionhub {label}: subset is INCOMPLETE, {len(motion_set & caption_set)} of "
            f"{len(captions)} captioned motions have their .npz here and the remote repository "
            f"lists {remote} files for it, so this source is a partial download"
        )
    return clips, skipped


def _walk_stems(base: Path, dir_depth: int, suffix: str) -> list[str]:
    """Sorted POSIX-relative stems of every ``suffix`` file exactly ``dir_depth`` levels down.

    Explicit levels rather than ``rglob`` for two reasons: the layout is a fixed depth, so a file
    at the wrong depth is a surprise worth ignoring rather than silently ingesting, and sorting at
    each level makes the walk order a property of the names instead of the filesystem.
    """
    if not base.is_dir():
        return []
    level: list[tuple[Path, str]] = [(base, "")]
    for _ in range(dir_depth):
        deeper: list[tuple[Path, str]] = []
        for parent, prefix in level:
            for child in sorted(parent.iterdir(), key=lambda p: p.name):
                if child.is_dir():
                    deeper.append((child, f"{prefix}{child.name}/"))
        level = deeper
    stems: list[str] = []
    for parent, prefix in level:
        for child in sorted(parent.iterdir(), key=lambda p: p.name):
            if child.is_file() and child.suffix == suffix:
                stems.append(f"{prefix}{child.stem}")
    return sorted(stems)


def _absence_lines(
    *, label: str, missing: list[str], total: int, noun: str, reason: str
) -> list[str]:
    """One line per directory that is short of files, naming the files when there are few.

    Grouped by directory because HumanML3D_AMASS is short tens of thousands of files and a line
    each would bury the manifest. Both counts are always present, so the size of the gap is never
    hidden.
    """
    if not missing:
        return []
    groups: dict[str, list[str]] = {}
    for stem in missing:
        head = stem.split("/", 1)[0]
        groups.setdefault(head, []).append(stem)
    lines: list[str] = []
    for head in sorted(groups):
        names = groups[head]
        if len(names) <= _NAMED_ABSENCE_LIMIT:
            lines.append(f"motionhub {label} {head}: {reason}: {', '.join(names)}")
        else:
            lines.append(
                f"motionhub {label} {head}: {len(names)} {noun} in this directory have "
                f"{reason} ({total} {noun} present in the subset, "
                f"{len(missing)} short across the subset)"
            )
    return lines


def _build_clip(
    root: Path,
    base: Path,
    subset: _Subset,
    stem: str,
    ingested_at: str,
    seen: dict[str, str],
) -> tuple[ReferenceClip | None, str | None]:
    """One clip, or None and a reason. Never raises on a bad file: a partial download is normal."""
    label = _SUBSET_LABELS[subset.directory]
    npz_path = base / subset.directory / "smplh_52" / f"{stem}.npz"
    json_path = base / subset.directory / "hierarchical_caption" / f"{stem}.json"

    clip_id = _clip_id(subset, stem)
    if len(clip_id) > _MAX_CLIP_ID:
        return None, f"motionhub {label} {stem}: clip id {clip_id!r} is longer than {_MAX_CLIP_ID}"
    if clip_id in seen:
        return None, (
            f"motionhub {label} {stem}: clip id {clip_id!r} collides with {seen[clip_id]!r}"
        )

    source_ref = f"{subset.directory}/smplh_52/{stem}.npz"
    if len(source_ref) > _MAX_SOURCE_REF:
        return None, f"motionhub {label} {stem}: source_ref is longer than {_MAX_SOURCE_REF}"

    motion, motion_error = _read_motion(npz_path)
    if motion is None:
        return None, f"motionhub {label} {stem}: {motion_error}"
    caption, caption_error = _read_caption(json_path)
    if caption is None:
        return None, f"motionhub {label} {stem}: {caption_error}"

    npz_rel = npz_path.relative_to(root).as_posix()
    files = (
        ReferenceFile(
            role="smpl",
            path=npz_rel,
            sha256=file_sha256(npz_path),
            size_bytes=npz_path.stat().st_size,
            person_index=0,
        ),
        ReferenceFile(
            role="annotation",
            path=json_path.relative_to(root).as_posix(),
            sha256=file_sha256(json_path),
            size_bytes=json_path.stat().st_size,
            person_index=0,
        ),
    )

    seen[clip_id] = stem
    return (
        ReferenceClip(
            clip_id=clip_id,
            source=subset.source,
            source_ref=source_ref,
            modality=Modality.mocap_smplh,
            # Parameters only: the conversion kept SMPL-H pose, translation and betas and no
            # imagery at all, so the geometry can drive a shot and there is nothing to look at.
            usage=UsageClass.pose_derivable,
            # One body per file, in its own world frame. See the module docstring for the numbers.
            people_count=1,
            affection=_affection(caption.category),
            # A single body has nobody to interact with. Tagging these with a two-person verb would
            # make a search for a hug return a person hugging the air, so the honest tag is
            # no_contact and the caption carries what the body is actually doing.
            interaction_tags=(InteractionTag.no_contact,),
            contact_tags=subset.contact_tags,
            postures=_postures(caption.text, motion),
            setting=subset.setting,
            frame_count=motion.frame_count,
            native_fps=motion.native_fps,
            duration_s=motion.frame_count / motion.native_fps,
            pose_format="smplh",
            pose_root=npz_rel,
            caption=caption.text,
            caption_source="dataset",
            measured={
                "root_height_median_m": round(motion.root_height_median_m, 4),
                "root_height_min_m": round(motion.root_height_min_m, 4),
                "root_height_max_m": round(motion.root_height_max_m, 4),
                "travel_m": round(motion.travel_m, 4),
                "horizontal_speed_mps": round(motion.speed_mps, 4),
            },
            files=files,
            ingested_at=ingested_at,
            ingester_version=INGESTER_VERSION,
        ),
        None,
    )


def _clip_id(subset: _Subset, stem: str) -> str:
    """A readable, pattern-legal id, e.g. ``mh_ego_20210929_s15_s11_03_b0_000``."""
    shortened = stem
    for old, new in subset.id_replacements:
        shortened = shortened.replace(old, new)
    slug = "".join(c if c in _ID_CHARS else "_" for c in shortened.lower())
    return f"{subset.id_prefix}_{slug}"


def _read_motion(path: Path) -> tuple[_Motion | None, str | None]:
    """Frame count, frame rate and the root-trajectory numbers, or None and why not.

    A truncated or half-written ``.npz`` is expected here rather than exceptional, because
    HumanML3D_AMASS is still downloading, so every read failure becomes a reason string.
    """
    try:
        with np.load(path, allow_pickle=False) as data:
            transl = np.asarray(data["transl"], dtype=np.float64)
            declared_frames = int(data["num_frames"])
            fps = float(data["mocap_framerate"])
    except (OSError, EOFError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return None, f"unreadable .npz ({type(exc).__name__}), possibly still downloading"

    if transl.ndim != 2 or transl.shape[1] != 3:
        return None, f"transl has shape {transl.shape}, expected (frames, 3)"
    frames = int(transl.shape[0])
    if frames < 1:
        return None, "no frames"
    if frames != declared_frames:
        return None, f"num_frames says {declared_frames} but transl holds {frames} rows"
    if not fps > 0.0:
        return None, f"mocap_framerate is {fps}"

    # y is up in these files: the conversion normalises the floor to y=0 and the horizontal track
    # lives in x and z, confirmed by a standing motion holding y near 1.1 m while x and z travel.
    height = transl[:, 1]
    steps = np.diff(transl[:, [0, 2]], axis=0)
    travel = float(np.linalg.norm(steps, axis=1).sum()) if frames > 1 else 0.0
    duration = frames / fps
    return (
        _Motion(
            frame_count=frames,
            native_fps=fps,
            root_height_median_m=float(np.median(height)),
            root_height_min_m=float(height.min()),
            root_height_max_m=float(height.max()),
            travel_m=travel,
            speed_mps=travel / duration,
        ),
        None,
    )


def _read_caption(path: Path) -> tuple[_Caption | None, str | None]:
    """The caption text and its category, or None and why not."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable caption .json ({type(exc).__name__})"
    if not isinstance(document, dict):
        return None, "caption .json is not an object"
    text = _caption_text(document)
    if not text:
        return None, "caption .json has no action or macro text"
    category = document.get("category")
    return _Caption(text=text, category=category if isinstance(category, str) else ""), None


def _caption_text(document: dict[str, Any]) -> str:
    """The action phrase then the first macro, meso and micro sentence, capped at 600 characters.

    Greedy from the coarsest level down, and a part that would overflow the cap is dropped whole,
    so a caption is never cut mid-word and the same document always yields the same string.
    """
    candidates: list[str] = []
    action = document.get("action")
    if isinstance(action, str) and action.strip():
        candidates.append(action.strip())
    for key in ("macro", "meso", "micro"):
        value = document.get(key)
        if isinstance(value, list) and value and isinstance(value[0], str) and value[0].strip():
            candidates.append(value[0].strip())

    kept: list[str] = []
    length = 0
    for part in candidates:
        extra = len(part) + (1 if kept else 0)
        if length + extra > _MAX_CAPTION:
            continue
        kept.append(part)
        length += extra
    return " ".join(kept)


def _affection(category: str) -> Affection:
    """Aggression for the combat category, neutral for everything else.

    Never ``affection``: one body on its own has no partner to be affectionate with, so claiming it
    would put solo motion in front of a query for tenderness.
    """
    if category == _AGGRESSION_CATEGORY:
        return Affection.aggression
    return Affection.neutral


def _postures(caption: str, motion: _Motion) -> tuple[Posture, ...]:
    """Postures for one clip, sorted by value the way the contract requires.

    The caption's own words first, because the dataset states the posture and the root height does
    not separate sitting from standing in EgoBody. Only when no posture word appears does the
    measured geometry decide, which is what happens for nearly every GRAB take.
    """
    tokens = set(_WORD.findall(caption.lower()))
    named = {posture for posture, words in _POSTURE_TOKENS if tokens & words}
    if named:
        return tuple(sorted(named, key=lambda posture: posture.value))
    return _measured_postures(motion)


def _measured_postures(motion: _Motion) -> tuple[Posture, ...]:
    """The fallback: root height for the base posture, horizontal path speed for locomotion."""
    if motion.root_height_median_m < _LYING_MAX_M:
        base = Posture.lying
    elif motion.root_height_median_m < _STANDING_MIN_M:
        base = Posture.sitting
    else:
        base = Posture.standing

    postures = {base}
    # Only an upright body can be walking or running; a low root that travels is crawling or
    # rolling, and this module has no way to tell those apart.
    if base is Posture.standing:
        if motion.speed_mps >= _RUN_MIN_MPS:
            postures.add(Posture.running)
        elif motion.speed_mps >= _WALK_MIN_MPS:
            postures.add(Posture.walking)
    return tuple(sorted(postures, key=lambda posture: posture.value))
