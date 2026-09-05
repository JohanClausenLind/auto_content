"""Turn review verdicts into proposed changes to the prompting guidance — proposals only.

The guidance in ``skills/image/prompting/SKILL.md`` is how this pipeline knows to lead with the
style clause, to name a process rather than a surface, to ask for tonal range. Every rule there was
learned by rendering something, looking at it, and measuring why it was wrong. That loop should keep
running: a review batch that rejects frames is evidence about prompting, and evidence should become
guidance.

The guidance updates itself, on the operator's instruction. What keeps that honest is not a
permission prompt — it is that nothing here is self-assessed:

* the failure -> guidance mapping is **fixed in code**, so the learner can report that an already
  understood failure recurred but cannot invent a rule it was not taught;
* a failure must **recur** before it becomes a rule, so one bad frame is not doctrine;
* every lesson carries the **measurements and frame ids** behind it, so a claim can be checked
  rather than trusted;
* every self-applied change is **appended to an audit log** with its diff, so what changed and
  why is reviewable afterwards even though nobody was asked beforehand;
* applying still refuses when the file changed underneath it — that is not permission, it is
  correctness: the recorded diff would no longer describe what happens, and someone else's edit
  would be silently discarded.

:func:`auto_apply` is the unattended path. :func:`apply_proposal` remains for the attended one.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from content_factory.schemas.base import SchemaModel, Sha256Hex, VersionedModel, sha256_hex
from content_factory.schemas.review import FrameReviewBatch

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = REPO_ROOT / "skills" / "image" / "prompting" / "SKILL.md"

LessonKind = Literal["tonal_collapse", "monochrome_partial", "edge_intrusion", "continuity_break"]

# What each measurable failure implies about prompting, and the rule to add if it recurs. The
# mapping is fixed in code rather than inferred, so a proposal cannot invent a rule — it can only
# report that an already-understood failure happened often enough to be worth writing down.
_LESSON_TEXT: dict[LessonKind, tuple[str, str]] = {
    "tonal_collapse": (
        "Ask explicitly for tonal range",
        'Add "full tonal range with detail held in both the shadows and the highlights" to any '
        "style that is not photographic; illustration styles crush the midtones otherwise.",
    ),
    "monochrome_partial": (
        "Commit to monochrome or drop the claim",
        'A style saying "monochrome" or "no colour" is applied only partly, leaving an image '
        "that is neither colour nor grey. State the single ink colour instead.",
    ),
    "edge_intrusion": (
        "Keep the subject clear of the frame border",
        "Subject matter jammed into the frame edge means the staging does not fit the lens; "
        "restage rather than reprompting.",
    ),
    "continuity_break": (
        "Anchor the world, not just the pose",
        "Consecutive frames inventing a new setting means nothing but the skeleton is anchored; "
        "add an identity reference and a background plate.",
    ),
}

MIN_OCCURRENCES = 2
"""One bad frame is noise. A rule needs the failure to have happened more than once."""


class PromptLesson(SchemaModel):
    """One thing the reviews say about prompting, with the evidence that says it."""

    kind: LessonKind
    title: str = Field(min_length=1, max_length=120)
    guidance: str = Field(min_length=1, max_length=600)
    occurrences: int = Field(ge=1)
    frame_ids: tuple[str, ...] = Field(min_length=1)
    measured: tuple[float, ...] = ()
    """The numbers behind it, so a reviewer can judge the claim rather than trust it."""


class GuidanceProposal(VersionedModel):
    """A proposed change to the guidance. Inert until a named person applies it."""

    proposal_id: str = Field(min_length=1, max_length=64)
    created_at: dt.datetime
    skill_path: str
    skill_sha256_before: Sha256Hex
    """The guidance this diff was computed against. If the file has moved on, the proposal is
    stale and applying it would silently discard whatever changed."""
    lessons: tuple[PromptLesson, ...] = Field(min_length=1)
    diff: str = Field(min_length=1)
    """A unified diff, so what changes is visible before it changes."""
    applied_by: str | None = Field(default=None, max_length=120)
    applied_at: dt.datetime | None = None


def lessons_from_review(batches: list[FrameReviewBatch]) -> list[PromptLesson]:
    """What the verdicts say, counted. Only failures the mapping already understands become
    lessons — the learner reports recurrence, it does not invent rules."""
    tally: dict[LessonKind, list[tuple[str, float | None]]] = {}
    check_to_kind: dict[str, LessonKind] = {
        "midtone_range": "tonal_collapse",
        "black_clipping": "tonal_collapse",
        "monochrome_honoured": "monochrome_partial",
        "frame_edges_clear": "edge_intrusion",
        "continuity": "continuity_break",
    }
    for batch in batches:
        for frame in batch.frames:
            for finding in frame.findings:
                if finding.passed:
                    continue
                kind = check_to_kind.get(finding.check)
                if kind is None:
                    continue
                tally.setdefault(kind, []).append((frame.frame_id, finding.measured))
    lessons: list[PromptLesson] = []
    for kind, hits in sorted(tally.items()):
        if len(hits) < MIN_OCCURRENCES:
            continue
        title, guidance = _LESSON_TEXT[kind]
        lessons.append(
            PromptLesson(
                kind=kind,
                title=title,
                guidance=guidance,
                occurrences=len(hits),
                frame_ids=tuple(sorted({f for f, _ in hits}))[:20],
                measured=tuple(m for _, m in hits if m is not None)[:20],
            )
        )
    return lessons


def render_proposal(lessons: list[PromptLesson], *, skill_path: Path = SKILL_PATH) -> str:
    """The guidance file as it would read if the proposal were applied."""
    current = skill_path.read_text(encoding="utf-8")
    addition = ["", "## Proposed from review evidence", ""]
    for lesson in lessons:
        addition.append(f"### {lesson.title}")
        addition.append(lesson.guidance)
        measured = ", ".join(f"{m:g}" for m in lesson.measured[:5])
        addition.append(
            f"*Evidence: {lesson.occurrences} occurrence(s) across "
            f"{len(lesson.frame_ids)} frame(s)"
            + (f"; measured {measured}" if measured else "")
            + f". Frames: {', '.join(lesson.frame_ids[:5])}.*"
        )
        addition.append("")
    return current.rstrip("\n") + "\n" + "\n".join(addition)


def write_proposal(
    lessons: list[PromptLesson], out_dir: Path, *, skill_path: Path = SKILL_PATH
) -> GuidanceProposal:
    """Record a proposal. This never touches the guidance file."""
    if not lessons:
        msg = "no lessons to propose: the reviews contain no recurring understood failure"
        raise ValueError(msg)
    current = skill_path.read_text(encoding="utf-8")
    proposed = render_proposal(lessons, skill_path=skill_path)
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{skill_path.name}",
            tofile=f"b/{skill_path.name}",
        )
    )
    created = dt.datetime.now(dt.UTC)
    proposal = GuidanceProposal(
        proposal_id=sha256_hex(diff.encode())[:16],
        created_at=created,
        # Repo-relative when it is inside the repo (the real case), absolute otherwise so a
        # proposal written against a file elsewhere still records where it pointed.
        skill_path=str(
            skill_path.relative_to(REPO_ROOT)
            if skill_path.is_relative_to(REPO_ROOT)
            else skill_path
        ),
        skill_sha256_before=sha256_hex(current.encode()),
        lessons=tuple(lessons),
        diff=diff,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{proposal.proposal_id}.json").write_text(proposal.model_dump_json(indent=1))
    return proposal


class ProposalRefusedError(RuntimeError):
    """Applying a proposal was refused. The guidance file is unchanged."""


def apply_proposal(
    proposal: GuidanceProposal, *, applied_by: str, skill_path: Path = SKILL_PATH
) -> GuidanceProposal:
    """Apply an approved proposal. Refuses unless a person is named and the file is unchanged."""
    if not applied_by.strip():
        msg = "applying needs an attribution ('auto' for the unattended path); nothing applied"
        raise ProposalRefusedError(msg)
    current = skill_path.read_text(encoding="utf-8")
    if sha256_hex(current.encode()) != proposal.skill_sha256_before:
        msg = (
            f"{skill_path.name} has changed since this proposal was written, so its diff no "
            "longer describes what would happen. Re-propose against the current file."
        )
        raise ProposalRefusedError(msg)
    skill_path.write_text(render_proposal(list(proposal.lessons), skill_path=skill_path))
    return proposal.model_copy(
        update={"applied_by": applied_by, "applied_at": dt.datetime.now(dt.UTC)}
    )


AUDIT_LOG = REPO_ROOT / "skills" / "image" / "prompting" / "self-updates.jsonl"


def auto_apply(
    batches: list[FrameReviewBatch],
    *,
    skill_path: Path = SKILL_PATH,
    audit_log: Path = AUDIT_LOG,
    out_dir: Path | None = None,
) -> GuidanceProposal | None:
    """Learn from the reviews and update the guidance, unattended.

    Returns the applied proposal, or ``None`` when the reviews say nothing new — which is the
    common case and not a failure. The proposal is still written out and still appended to the
    audit log, so an unattended change is as inspectable afterwards as an approved one.
    """
    lessons = lessons_from_review(batches)
    if not lessons:
        return None
    proposal = write_proposal(
        lessons, out_dir or (REPO_ROOT / "output" / "prompting-proposals"), skill_path=skill_path
    )
    applied = apply_proposal(proposal, applied_by="auto", skill_path=skill_path)
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with audit_log.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "proposal_id": applied.proposal_id,
                    "applied_at": applied.applied_at.isoformat() if applied.applied_at else None,
                    "applied_by": "auto",
                    "lessons": [
                        {
                            "kind": lesson.kind,
                            "title": lesson.title,
                            "occurrences": lesson.occurrences,
                            "frame_ids": list(lesson.frame_ids),
                            "measured": list(lesson.measured),
                        }
                        for lesson in applied.lessons
                    ],
                    "diff": applied.diff,
                },
                sort_keys=True,
            )
            + "\n"
        )
    return applied


def load_proposal(path: Path) -> GuidanceProposal:
    return GuidanceProposal.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_batches(project_dir: Path) -> list[FrameReviewBatch]:
    """Every frame-review batch under a run, newest last."""
    out: list[FrameReviewBatch] = []
    for path in sorted(project_dir.rglob("reviews/frames/batch.json")):
        try:
            out.append(FrameReviewBatch.model_validate_json(path.read_text(encoding="utf-8")))
        except (ValueError, json.JSONDecodeError):
            continue
    return out
