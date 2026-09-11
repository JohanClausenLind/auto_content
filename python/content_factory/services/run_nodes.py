"""A run read as its nodes: which step made which file, and what the run was rendering.

The history could show a run's files grouped by file type. That answers "what came out of this"
and not the question a person in front of a bad picture actually has — *which step made this, and
what else did that step make?* ComfyUI answers it by hanging every output off the node that
emitted it, and the answer is worth having here for the same reason: a drift-QC number and the
eight frames it is about belong on one node, not in two lists.

Two sources of attribution, and the reader is always told which it got:

* **recorded** — the run observed it. :mod:`content_factory.runners.attribution` snapshots the run
  directory between steps, so ``run.json`` carries the exact file list per node. Only runs made
  after 2026-09-11 have this.
* **inferred** — the path says it. The 241 reports already on disk represent weeks of GPU time and
  predate the recording, so ``anchors/`` is read as ``generate_anchor``'s work and
  ``audio/*.restored.wav`` as ``restore_speech``'s. This is a guess from a table, and it is
  labelled as one.

A file the table cannot place stays **unattributed** rather than being hung on a plausible node.
That is the same rule :func:`content_factory.services.durations.infer_workflow` follows for lanes:
an ambiguous answer is worse than no answer, because the wrong node is where somebody will look
for the cause of a fault that is somewhere else.

Nothing here writes anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Attribution = Literal["recorded", "inferred"]

# Where a file sits, and which stage could have put it there. Longest key wins, so
# `sequence/frames/` is the keyframes and plain `sequence/` is the sheet made from them. Every
# entry was read off the 241 reports on disk rather than off the stage list.
#
# Several keys name more than one candidate, because several lanes write to the same folder:
# `exports/` is a film on `narrated-video`, a card set on `single-clip-post` and a flipbook on
# `image-set`. The candidate list is resolved against the stages the run *actually ran*, and a
# path whose candidates leave two possibilities is left unattributed — see `attribute`.
_STAGES_BY_PATH: tuple[tuple[str, tuple[str, ...]], ...] = (
    # the run root
    ("story/", ("plan_story",)),
    ("uploads/", ("ingest",)),
    ("research/", ("research",)),
    ("research/claims.json", ("verify_claims",)),
    ("edits/", ("ingest",)),
    # pictures
    ("anchors/upscaled/", ("upscale_video", "interpolate")),
    ("anchors/frames/", ("generate_anchor",)),
    ("anchors/", ("generate_anchor",)),
    ("sequence/frames/", ("generate_keyframes",)),
    ("sequence/drift/", ("drift_qc",)),
    # `sequence/anchor.png` is the hub every spoke is edited from, copied out of `anchors/` by
    # `stage_generate_keyframes` itself. Named rather than left to the `sequence/` fallback, which
    # attributed it to `package_sequence` — a stage that had not run — on the first stopped run
    # this was tried against (2026-09-11). `lock_generation` is the second candidate because it is
    # the other stage that writes into `sequence/`, so on a lane holding both the file goes
    # unattributed instead of to whichever the table happened to name first.
    ("sequence/anchor.png", ("generate_keyframes", "lock_generation")),
    ("sequence/lock.json", ("lock_generation",)),
    ("sequence/", ("package_sequence",)),
    ("controls/", ("compile_controls",)),
    ("reference/", ("find_reference",)),
    ("shots/", ("plan_shots", "route_shots")),
    ("reviews/frames/", ("review_frames",)),
    ("reviews/assets/", ("review_assets",)),
    # sound. The directory is one stage's name and six stages' work, so these are by filename.
    ("audio/source.wav", ("ingest",)),
    ("audio/transcript", ("transcribe_audio",)),
    ("audio/alignment.json", ("align_words",)),
    ("audio/narration-stem.wav", ("synthesize_narration", "voice_over")),
    ("audio/narration-mastered.wav", ("mix_audio",)),
    ("audio/narration-with-music.wav", ("mix_audio",)),
    ("audio/narration-with-cues.wav", ("sound_design",)),
    ("audio/narration-with-sfx.wav", ("sound_design",)),
    ("audio/music-selection.json", ("select_music",)),
    ("audio/loudness.json", ("mix_audio",)),
    ("audio/cue-sheet.json", ("sound_design",)),
    ("audio/cues.wav", ("sound_design",)),
    ("audio/sfx", ("sound_design",)),
    ("audio/restore-work/", ("restore_speech",)),
    (".restored.wav", ("restore_speech",)),
    (".restoration.json", ("restore_speech",)),
    (".tts.json", ("synthesize_narration",)),
    (".take.json", ("voice_over",)),
    (".segment.json", ("synthesize_narration", "voice_over")),
    # words and film
    ("script/", ("lock_script", "write_copy")),
    ("captions/", ("compile_captions",)),
    ("timeline/", ("compile_timeline",)),
    ("video/", ("generate_video", "render_scenes")),
    ("generated/", ("generate_video",)),
    ("artboards/", ("compile_artboards",)),
    ("exports/", ("compose_video", "render_cards", "render_static", "export_article")),
    ("exports/final.mp4", ("upscale_video", "fix_video", "compose_video")),
    ("qc/accessibility.json", ("qc_deliverable",)),
    ("qc/package.json", ("package_qc",)),
    ("qc/", ("qc_deliverable",)),
    ("destination-packages/", ("compile_destination_packages",)),
)


def _candidates_for_path(relative: str) -> tuple[str, ...]:
    """The stages the table says could have written this file, by longest matching key."""
    best = ""
    found: tuple[str, ...] = ()
    for key, stages in _STAGES_BY_PATH:
        if key in relative and len(key) > len(best):
            best, found = key, stages
    return found


@dataclass(frozen=True)
class NodeRecord:
    """One step of a run, as a node the canvas can draw and a panel can open."""

    node: str
    """The lane's own key for this step — ``anchor``, ``spokes``, ``frames_gate``. The same key
    ``workflows/*.yaml`` uses and the runner reports, so a graph and a run agree about names."""
    stage: str
    ok: bool
    pinned: bool
    """Frozen, not produced by this run. Its files are the ones the run that made them left."""
    blocked: bool
    """It stopped for a person, not because it broke. The distinction is the whole reason a
    picture story parked at ``review_frames`` does not read as a failure."""
    ran: bool = True
    """Whether this run has a record at this node at all.

    A node the lane has and the run never reached is not a failure, and the two must not draw the
    same: a run resumed with ``--from`` shows every earlier node as *not run by this run*, with
    the files an earlier one left still hanging off it."""
    seconds: float = 0.0
    error: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    """What the stage said about its own work — attempt counts, backends, drift numbers. Shown on
    the node, because "3 attempts, backend hidream-o1" is the first thing to know about a frame
    that came out wrong."""
    outputs: list[str] = field(default_factory=list)
    """Paths, relative to the run directory. Resolved against the run's real files by the caller,
    so a path the run recorded but that is no longer there simply does not appear."""
    outputs_total: int = 0
    """How many files this node produced, which is not ``len(outputs)`` once either cap bites."""
    attribution: Attribution = "inferred"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stage_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (report.get("stages") or []) if isinstance(s, dict)]


def _lane_nodes(report: dict[str, Any], workflow: str | None) -> list[tuple[str, str]]:
    """``(node_key, stage)`` for the whole lane, in lane order, or as much as can be known.

    The list has to be the **lane's** nodes and not the ones this run executed, because a run
    started with ``--from`` executes a slice: ``ps1c-pinecone`` resumed at ``upscale_video`` and
    its twelve stage records begin there, so a list built from them has no ``anchor`` node — and
    every anchor drawing in the run then belongs to nobody. That is exactly the run somebody
    opens, because it is the one that needed a resume.

    Three sources, most authoritative first: the lane definition (every node, in order, with its
    stage); the report's own ``steps`` record, which the runner merges across resumes and so
    carries nodes this slice never ran, though a carried node's stage is recorded empty; and
    finally the stage records, which is all a report from before either feature has.
    """
    executed = [
        (str(e["node"]), str(e["stage"]))
        for e in _stage_entries(report)
        if isinstance(e.get("node"), str) and isinstance(e.get("stage"), str) and e["stage"]
    ]
    if workflow:
        try:
            from content_factory.workflows.catalog import stage_order

            lane = [(key, stage.value) for key, stage, _values in stage_order(workflow)]
        except Exception:
            lane = []  # a lane renamed or deleted since the run: fall through to the report
        if lane:
            known = {key for key, _stage in lane}
            # A node the run executed that the lane no longer has is appended rather than dropped:
            # the files are there, and a definition edited after the run must not hide them.
            return lane + [(key, stage) for key, stage in executed if key not in known]
    recorded_steps = [
        (str(e["node"]), str(e.get("stage") or ""))
        for e in (report.get("steps") or [])
        if isinstance(e, dict) and isinstance(e.get("node"), str) and e["node"]
    ]
    if recorded_steps:
        by_node = dict(executed)
        return [(key, stage or by_node.get(key, "")) for key, stage in recorded_steps]
    return executed


def node_records(report: dict[str, Any], *, workflow: str | None = None) -> list[NodeRecord]:
    """The lane's nodes in order, each carrying what this run did at it.

    A node the lane has and this run did not reach is present with ``ok`` false and no error: it
    is a step that has not happened, which is different from one that failed, and a canvas needs
    to draw the whole lane either way.
    """
    ran = {
        str(entry["node"]): entry
        for entry in _stage_entries(report)
        if isinstance(entry.get("node"), str) and entry["node"]
    }
    # A report from before the runner keyed steps by node has stage names where node keys go.
    ran.update(
        {
            str(entry["stage"]): entry
            for entry in _stage_entries(report)
            if not isinstance(entry.get("node"), str) and isinstance(entry.get("stage"), str)
        }
    )
    out: list[NodeRecord] = []
    seen: set[str] = set()
    for node, stage in _lane_nodes(report, workflow or report.get("workflow")):
        if node in seen:
            continue
        seen.add(node)
        entry = ran.get(node) or {}
        recorded = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else None
        paths = [str(p) for p in (recorded or {}).get("paths") or []]
        out.append(
            NodeRecord(
                node=node,
                stage=stage or str(entry.get("stage") or ""),
                ok=bool(entry.get("ok")),
                pinned=bool(entry.get("pinned")),
                blocked=isinstance(entry.get("blocked"), dict),
                ran=bool(entry),
                seconds=float(entry.get("seconds") or 0.0),
                error=entry.get("error") if isinstance(entry.get("error"), str) else None,
                facts=facts if isinstance(facts := entry.get("facts"), dict) else {},
                outputs=paths,
                outputs_total=int((recorded or {}).get("total") or len(paths)),
                attribution="recorded" if recorded is not None else "inferred",
            )
        )
    return out


def attribute(
    report: dict[str, Any], paths: list[str], *, workflow: str | None = None
) -> dict[str, tuple[str, str, Attribution]]:
    """``path -> (node, stage, how)`` for every path either source can place.

    The recorded lists win wherever they exist, because they are what the run observed; the table
    fills in the rest, which for every run made before 2026-09-11 is all of it. A path neither can
    place is absent from the mapping — the caller shows it as unattributed rather than guessing.

    Both sources speak the same spelling, so nothing is rebased: the runner observes paths
    relative to the *project* directory, the history addresses files relative to the *run*
    directory, and for a report at ``deliverables/<id>/run.json`` those are one tree
    (``run_history._record`` walks up to it).
    """
    out: dict[str, tuple[str, str, Attribution]] = {}
    known = set(paths)
    records = node_records(report, workflow=workflow)

    for record in records:
        if record.attribution != "recorded":
            continue
        for path in record.outputs:
            if path in known:
                out[path] = (record.node, record.stage, "recorded")

    # The table, for everything the run did not record. Attributed only to a node the lane
    # actually has: an `exports/` file in a lane with no `compose_video` belongs to whichever of
    # the candidates that lane does have, and to nobody when it has two of them or none. Hanging
    # it on an absent node would invent a step, and hanging it on the first plausible one would
    # send somebody looking for a fault at a stage that never ran.
    by_stage: dict[str, list[NodeRecord]] = {}
    for record in records:
        by_stage.setdefault(record.stage, []).append(record)
    for path in paths:
        if path in out:
            continue
        matched = [
            record for stage in _candidates_for_path(path) for record in by_stage.get(stage, ())
        ]
        if len(matched) != 1:
            continue
        out[path] = (matched[0].node, matched[0].stage, "inferred")
    return out


# --- what the run was rendering -----------------------------------------------------------------

_SUBJECT_KEYS = ("topic", "subject", "prompt")
"""Widget names that say what a run is about, in the order they are believed.

``topic`` is the brief's own word for it and the one every lane carries. ``prompt`` is the last
resort and the least reliable — on ``image-set`` it is the anchor's framing instruction ("the
subject alone, centred, plain background") rather than the subject — so it is only read when no
brief said anything.
"""

MAX_SUBJECT = 120


def _clean(text: object) -> str | None:
    """One line, trimmed to something a list row can hold."""
    if not isinstance(text, str):
        return None
    line = " ".join(text.split())
    if not line:
        return None
    return line if len(line) <= MAX_SUBJECT else line[: MAX_SUBJECT - 1].rstrip() + "…"


def subject(run_dir: Path, report: dict[str, Any]) -> str | None:
    """What this run was making, in the operator's own words, or None.

    A history of 241 rows called ``a20-imageset-owl`` and ``m04-picture-story-24`` is a list of
    directory names: it says which lane ran and nothing about what came out. The brief's topic is
    what the operator typed, so it is read first; the story plan's subject is what the lane made
    of it, and is the answer for a run started from a fixture with no brief widget set.

    Read from the report before the disk: the report is one file that is already open, and a run
    whose story plan has been cleared away still knows what it was asked for.
    """
    for entry in report.get("steps") or []:
        if not isinstance(entry, dict):
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        for key in _SUBJECT_KEYS[:2]:
            found = _clean(values.get(key))
            if found:
                return found
    plan = run_dir / "story" / "plan.json"
    try:
        story = json.loads(plan.read_text())
    except (OSError, ValueError):
        story = {}
    if isinstance(story, dict):
        for key in ("visual_subject", "hook_text"):
            found = _clean(story.get(key))
            if found:
                return found
        beats = story.get("beats")
        if isinstance(beats, list) and beats and isinstance(beats[0], dict):
            found = _clean(beats[0].get("display_text") or beats[0].get("spoken_text"))
            if found:
                return found
    for entry in report.get("steps") or []:
        if isinstance(entry, dict) and isinstance(entry.get("values"), dict):
            found = _clean(entry["values"].get("prompt"))
            if found:
                return found
    return None
