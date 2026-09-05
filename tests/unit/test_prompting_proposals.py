"""Prompt guidance learns from review verdicts and never edits itself.

The safety property under test is narrow and load-bearing: a system that rewrites its own
instructions from its own output has no check on it — one bad inference becomes a rule, the rule
shapes the next output, and the evidence for the rule is now circular. So the learner may only
write a proposal, and applying one requires a named person and an unchanged file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from content_factory.prompting import (
    ProposalRefusedError,
    apply_proposal,
    lessons_from_review,
    write_proposal,
)
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.review import FrameFinding, FrameRecord, FrameReviewBatch

SKILL = "# Guidance\n\n## Rules\n\n### 1. Existing rule\nSomething already learned.\n"


def _skill(tmp_path: Path, text: str = SKILL) -> Path:
    p = tmp_path / "SKILL.md"
    p.write_text(text)
    return p


def _batch(*findings: tuple[str, str, bool, float]) -> FrameReviewBatch:
    frames = tuple(
        FrameRecord(
            frame_id=f"sht_{i:03d}",
            png_sha256=f"{i:064d}",
            findings=(
                FrameFinding(
                    check=check,
                    passed=passed,
                    severity=severity,  # type: ignore[arg-type]
                    detail=f"{check} on frame {i}",
                    measured=measured,
                ),
            ),
        )
        for i, (check, severity, passed, measured) in enumerate(findings)
    )
    return FrameReviewBatch(
        deliverable_id="dlv_x000000000001",
        contact_sheet_sha256="d" * 64,
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=frames,
        created_at=dt.datetime.now(dt.UTC),
    )


def test_one_bad_frame_is_noise_and_produces_no_lesson() -> None:
    """A rule needs a failure to recur. Otherwise every one-off becomes doctrine."""
    batch = _batch(("midtone_range", "advisory", False, 0.21))
    assert lessons_from_review([batch]) == []


def test_a_recurring_measured_failure_becomes_a_lesson_with_its_evidence() -> None:
    batch = _batch(
        ("midtone_range", "advisory", False, 0.21),
        ("black_clipping", "advisory", False, 0.44),
        ("monochrome_honoured", "blocker", False, 0.39),
    )
    lessons = lessons_from_review([batch])
    kinds = {lesson.kind for lesson in lessons}
    # Two tonal findings collapse into one lesson; the single monochrome hit does not qualify.
    assert kinds == {"tonal_collapse"}
    tonal = next(lesson for lesson in lessons if lesson.kind == "tonal_collapse")
    assert tonal.occurrences == 2
    assert len(tonal.frame_ids) == 2
    assert tonal.measured == (0.21, 0.44), "the numbers travel with the claim"


def test_the_learner_cannot_invent_a_rule_it_was_not_taught() -> None:
    """Findings outside the fixed mapping are ignored rather than turned into novel guidance."""
    batch = _batch(
        ("some_new_check", "advisory", False, 1.0),
        ("another_new_check", "advisory", False, 2.0),
    )
    assert lessons_from_review([batch]) == []


def test_proposing_never_touches_the_guidance_file(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    before = skill.read_text()
    lessons = lessons_from_review(
        [_batch(("continuity", "advisory", False, 61.0), ("continuity", "advisory", False, 58.0))]
    )
    proposal = write_proposal(lessons, tmp_path / "proposals", skill_path=skill)
    assert skill.read_text() == before, "propose must be inert"
    assert proposal.diff.startswith("---")
    assert "continuity" not in before and "Anchor the world" in proposal.diff
    assert (tmp_path / "proposals" / f"{proposal.proposal_id}.json").exists()


def test_applying_requires_an_attribution(tmp_path: Path) -> None:
    """The guidance updates itself now, so nobody is asked first — but a change still has to be
    attributable, or the audit log records that something happened and not who or what did it."""
    skill = _skill(tmp_path)
    lessons = lessons_from_review(
        [_batch(("continuity", "advisory", False, 61.0), ("continuity", "advisory", False, 58.0))]
    )
    proposal = write_proposal(lessons, tmp_path / "p", skill_path=skill)
    with pytest.raises(ProposalRefusedError, match="needs an attribution"):
        apply_proposal(proposal, applied_by="   ", skill_path=skill)
    assert skill.read_text() == SKILL, "a refused apply leaves the file alone"


def test_the_unattended_path_applies_and_records_its_diff(tmp_path: Path) -> None:
    """auto_apply asks nobody, so the audit trail is the only thing standing between a
    self-applied rule and an unexplained one. It has to carry the diff and the evidence."""
    import json

    from content_factory.prompting import auto_apply

    skill = _skill(tmp_path)
    log = tmp_path / "self-updates.jsonl"
    batches = [
        _batch(("continuity", "advisory", False, 61.0), ("continuity", "advisory", False, 58.0))
    ]
    applied = auto_apply(batches, skill_path=skill, audit_log=log, out_dir=tmp_path / "proposals")
    assert applied is not None and applied.applied_by == "auto"
    assert "Anchor the world" in skill.read_text()
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["applied_by"] == "auto"
    assert entry["diff"].startswith("---"), "the audit entry must carry what changed"
    assert entry["lessons"][0]["measured"] == [61.0, 58.0], "and the evidence behind it"

    # Nothing recurring left to learn: a second pass is a no-op, not a duplicate append.
    assert auto_apply([], skill_path=skill, audit_log=log, out_dir=tmp_path / "proposals") is None
    assert len(log.read_text().splitlines()) == 1


def test_a_stale_proposal_is_refused_rather_than_clobbering(tmp_path: Path) -> None:
    """If the guidance moved on, the diff no longer describes what would happen — applying it
    would silently discard whatever changed in between."""
    skill = _skill(tmp_path)
    lessons = lessons_from_review(
        [_batch(("continuity", "advisory", False, 61.0), ("continuity", "advisory", False, 58.0))]
    )
    proposal = write_proposal(lessons, tmp_path / "p", skill_path=skill)
    skill.write_text(SKILL + "\n### 2. A rule someone added meanwhile\nText.\n")
    edited = skill.read_text()
    with pytest.raises(ProposalRefusedError, match="has changed since"):
        apply_proposal(proposal, applied_by="operator", skill_path=skill)
    assert skill.read_text() == edited, "the other edit survives"


def test_an_approved_proposal_applies_and_records_who(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    lessons = lessons_from_review(
        [
            _batch(
                ("frame_edges_clear", "advisory", False, 0.7),
                ("frame_edges_clear", "advisory", False, 0.8),
            )
        ]
    )
    proposal = write_proposal(lessons, tmp_path / "p", skill_path=skill)
    applied = apply_proposal(proposal, applied_by="operator", skill_path=skill)
    text = skill.read_text()
    assert "Keep the subject clear of the frame border" in text
    assert "### 1. Existing rule" in text, "existing guidance is kept, not replaced"
    assert applied.applied_by == "operator" and applied.applied_at is not None
    # The file genuinely changed, so the proposal's own "before" digest no longer matches it —
    # which is what makes re-applying the same proposal a refusal rather than a duplicate append.
    assert sha256_hex(text.encode()) != proposal.skill_sha256_before
    with pytest.raises(ProposalRefusedError, match="has changed since"):
        apply_proposal(proposal, applied_by="operator", skill_path=skill)
