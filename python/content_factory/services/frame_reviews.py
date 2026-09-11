"""The drawings a run is waiting on somebody to look at, and the verdict they record.

Thirteen runs on this machine are parked at ``review_frames`` with their pictures finished: the
gate did its job, wrote a contact sheet, and stopped. Answering it meant knowing the path on disk
and typing ``content-factory frames review`` — so the images that most needed a person were the
ones hardest to reach, and 27 runs sat overnight with nobody coming.

This module is what the workspace's review panel reads and writes. It is deliberately thin:

* **The batch on disk is the question.** ``reviews/frames/batch.json`` is written by the gate;
  ``verdict.json`` beside it is the answer. The current state is the first with the second laid
  over it, matched by image digest (:func:`content_factory.qc.verdict.merge_verdict`) — the same
  merge the stage does, so the panel and the gate never disagree about what is outstanding.
* **The rules live in one place.** Who may accept a batch unopened, what a verdict has to name,
  what the contract will read back: all of that is :mod:`content_factory.qc.verdict`, shared with
  the CLI.
* **Paths only.** No run ids, no HTTP, no database — the route decodes the run id and this takes
  the directory, which keeps it usable from a test, a script and the API alike.

Nothing here starts or resumes a run: recording a verdict unblocks the gate, and the run itself is
continued with the command in :func:`resume_command`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from content_factory.qc.verdict import decide, merge_verdict
from content_factory.schemas.base import file_sha256
from content_factory.schemas.review import FrameRecord, FrameReviewBatch, ReviewerKind

REVIEW_REL = Path("reviews") / "frames"
"""Where the gate writes, relative to a deliverable directory."""

GATE_STAGE = "review_frames"


@dataclass(frozen=True)
class ReviewFrame:
    """One drawing the reviewer is being asked about."""

    frame_id: str
    verdict: str
    reason: str
    image: str | None
    """Relative to the run directory, so it is fetched by the same route as every other output.
    ``None`` when the batch names a frame whose file is not where the run left it."""
    png_sha256: str
    on_disk: bool
    """Whether the file still hashes to what the batch decided on. A ``False`` here means the
    picture was regenerated after the gate ran and the panel is not showing what it would be
    approving — the gate would refuse that verdict on the next pass anyway."""
    blocked: bool
    """A measurement failed on its own. Such a frame fails whatever anybody says about it."""
    findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RunReview:
    """One deliverable's frame-review gate, as the panel shows it."""

    deliverable: str
    """The deliverable directory's name — the id the verdict is recorded against."""
    deliverable_id: str
    contact_sheet: str | None
    reviewer: str | None
    reviewed_at: str | None
    notes: str
    passed: bool
    frames: list[ReviewFrame]
    unreviewed: int
    rejected: int
    accepted: int
    flagged: int
    """Frames carrying at least one failed measurement — advisory ones included, because they are
    what a reviewer should look at first."""

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "frames": [asdict(f) for f in self.frames]}


def batch_path(deliverable_dir: Path) -> Path:
    return deliverable_dir / REVIEW_REL / "batch.json"


def verdict_path(deliverable_dir: Path) -> Path:
    return deliverable_dir / REVIEW_REL / "verdict.json"


def current_batch(deliverable_dir: Path) -> FrameReviewBatch | None:
    """The question with the answer laid over it, or None when this deliverable has no gate.

    A malformed batch is None rather than an exception: a run writing its report while the panel
    polls is normal, and one unreadable file must not take the whole list down with it.
    """
    path = batch_path(deliverable_dir)
    try:
        batch = FrameReviewBatch.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        prior = FrameReviewBatch.model_validate_json(verdict_path(deliverable_dir).read_text())
    except (OSError, ValueError):
        return batch
    return merge_verdict(batch, prior)


def frame_image(deliverable_dir: Path, frame_id: str) -> Path | None:
    """The picture a frame id names, as the two lanes lay them out.

    ``frame:0007`` is a keyframe (``sequence/frames/0007.png``); ``shot_ab54…:0000`` is a per-shot
    anchor, whose path the anchor manifest carries — read rather than guessed, because a lane may
    write more than one frame per shot.
    """
    head, _, tail = frame_id.rpartition(":")
    if not head:
        return None
    if head == "frame":
        candidate = deliverable_dir / "sequence" / "frames" / f"{tail}.png"
        return candidate if candidate.is_file() else None
    manifest = deliverable_dir / "anchors" / "manifest.json"
    try:
        shots = json.loads(manifest.read_text())["shots"]
    except (OSError, ValueError, KeyError, TypeError):
        shots = []
    for shot in shots:
        if str(shot.get("shot_id")) != head:
            continue
        for frame in shot.get("frames") or []:
            if f"{int(frame.get('frame_index', -1)):04d}" != tail:
                continue
            candidate = deliverable_dir / str(frame.get("path", ""))
            return candidate if candidate.is_file() else None
    return None


def _as_frame(run_dir: Path, deliverable_dir: Path, record: FrameRecord) -> ReviewFrame:
    image = frame_image(deliverable_dir, record.frame_id)
    return ReviewFrame(
        frame_id=record.frame_id,
        verdict=record.verdict,
        reason=record.reason,
        image=image.relative_to(run_dir).as_posix() if image is not None else None,
        png_sha256=record.png_sha256,
        # Hashed, not assumed. The verdict binds to the digest, so a panel showing a file that no
        # longer matches would be asking for a decision about a picture nobody can see.
        on_disk=image is not None and file_sha256(image) == record.png_sha256,
        blocked=record.blocked,
        findings=[f.model_dump(mode="json") for f in record.findings],
    )


def review(run_dir: Path, deliverable_dir: Path) -> RunReview | None:
    """One deliverable's gate, with every frame resolved to a file the browser can fetch."""
    batch = current_batch(deliverable_dir)
    if batch is None:
        return None
    sheet = deliverable_dir / batch.contact_sheet_path
    frames = [_as_frame(run_dir, deliverable_dir, record) for record in batch.frames]
    return RunReview(
        deliverable=deliverable_dir.name,
        deliverable_id=batch.deliverable_id,
        contact_sheet=sheet.relative_to(run_dir).as_posix() if sheet.is_file() else None,
        reviewer=batch.reviewer,
        reviewed_at=batch.reviewed_at.isoformat() if batch.reviewed_at else None,
        notes=batch.notes,
        passed=batch.passed,
        frames=frames,
        unreviewed=len(batch.unreviewed),
        rejected=len(batch.rejected),
        accepted=sum(1 for f in batch.frames if f.verdict == "accept"),
        flagged=sum(1 for f in batch.frames if any(not finding.passed for finding in f.findings)),
    )


def deliverable_dirs(run_dir: Path) -> list[Path]:
    """The deliverables of a run that have a frame-review gate, in a stable order."""
    return [
        path.parent.parent.parent
        for path in sorted(run_dir.glob("deliverables/*/reviews/frames/batch.json"))
    ]


def run_reviews(run_dir: Path) -> list[RunReview]:
    """Every gate in this run. Usually one; a run may carry several deliverables."""
    found = [review(run_dir, deliverable) for deliverable in deliverable_dirs(run_dir)]
    return [item for item in found if item is not None]


def waiting_frames(run_dir: Path) -> int:
    """How many drawings in this run nobody has decided about yet.

    Cheap enough for a list of a hundred runs: only a run that reached the gate has a batch to
    read, and a batch is a few kilobytes describing six pictures.
    """
    total = 0
    for deliverable in deliverable_dirs(run_dir):
        batch = current_batch(deliverable)
        if batch is not None:
            total += len(batch.unreviewed)
    return total


def resume_command(workflow: str | None, project_dir: str) -> str:
    """What continues a run once its gate is answered.

    A verdict unblocks the gate; it does not restart the stages after it, and pretending otherwise
    would leave a reviewer waiting for a film that nothing is making. The stages before
    ``review_frames`` are cached, so this costs the pictures nothing.
    """
    return (
        f"content-factory run-local {workflow or '<workflow>'}"
        f" --project-dir {project_dir} --from {GATE_STAGE}"
    )


def record_verdict(
    run_dir: Path,
    deliverable_dir: Path,
    *,
    reviewer: ReviewerKind,
    accept: list[str],
    reject: list[str],
    accept_rest: bool = False,
    reason: str = "",
    note: str = "",
) -> RunReview:
    """Write the verdict for one deliverable, or raise before anything reaches disk.

    Every rule that can refuse it is in ``qc.verdict`` (:class:`VerdictRefusedError`), so the panel
    and the CLI cannot come to different answers about what a verdict may say.

    The decision is taken on the *current* batch — question plus any answer already recorded — so
    a reviewer who rejected one frame yesterday is deciding today about what is actually left.
    """
    batch = current_batch(deliverable_dir)
    if batch is None:
        msg = f"no frame-review batch under {deliverable_dir}"
        raise FileNotFoundError(msg)
    decided = decide(
        batch,
        reviewer=reviewer,
        accept=accept,
        reject=reject,
        accept_rest=accept_rest,
        reason=reason,
        note=note,
    )
    target = verdict_path(deliverable_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(decided.model_dump_json(indent=1))
    decided_review = review(run_dir, deliverable_dir)
    if decided_review is None:  # pragma: no cover - the batch was readable a line ago
        msg = f"verdict written but the batch under {deliverable_dir} is no longer readable"
        raise FileNotFoundError(msg)
    return decided_review
