"""Who reviews this run's pictures — and, when it is an agent, what it has to do to say yes.

Both review stages park on a person, because passing measurements is necessary and never
sufficient: the faults this pipeline actually produced were two characters standing back to back,
four figures where two were staged, "honey-coloured" drawn as jars of honey. None of that is
measurable, all of it is obvious in the picture.

But a run started from a Claude Code session has an agent sitting right there, and 27 runs on this
machine were parked at ``review_frames`` with their drawings finished and nobody coming. So the
gate now asks *who is reviewing*, and when the answer is an agent it writes a request addressed to
it: every image by path, every measurement, and the one command that answers.

**The agent still has to look.** That is enforced rather than trusted: an agent verdict must name
every frame it decided (see ``missing_decisions``), so ``--accept-all`` — a reviewer saying yes to
a batch without opening it — is not available to one. A person keeps it, because a person is
looking at a contact sheet that shows all the frames in one image.

Detection is by ``CLAUDECODE``, which Claude Code sets in every command it runs. That makes "I
started this run with Claude" the actual trigger the operator asked for, rather than a flag
somebody has to remember. An explicit parameter always wins, and ``CF_REVIEWER`` overrides the
detection for a shell that wants a person in the loop anyway.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from content_factory.qc.frame_review import ConsistencyReport
from content_factory.schemas.review import FrameRecord, ReviewerKind

AGENT_ENV = "CLAUDECODE"
"""Set to "1" in every command a Claude Code session runs. The signal that an agent is present."""

OVERRIDE_ENV = "CF_REVIEWER"
"""`operator`, `agent` or `vlm`. For a Claude-started run whose pictures a person wants to see."""


def intended_reviewer(
    param: str | None = None, env: Mapping[str, str] | None = None
) -> ReviewerKind:
    """Who should review this run: an explicit choice, then the environment, then a person.

    Order matters and the default matters more: an unrecognised value falls through to
    ``operator`` rather than raising, because a typo in a reviewer name must not be able to skip
    a human review — the failure mode has to be "a person is asked" and never "nobody is".
    """
    environ = os.environ if env is None else env
    for candidate in (param, environ.get(OVERRIDE_ENV)):
        if candidate in {"operator", "agent", "vlm"}:
            return candidate  # type: ignore[return-value]
    if environ.get(AGENT_ENV):
        return "agent"
    return "operator"


def missing_decisions(
    frames: Sequence[FrameRecord], accepted: set[str], rejected: set[str]
) -> list[str]:
    """Frames an agent's verdict did not mention and which nobody has decided yet, in frame order.

    This is what makes "review every image" a rule rather than a hope. A person reviews from a
    contact sheet — one image showing all of them — and may reasonably say "all fine". An agent
    reads them one at a time, so a verdict that does not name a frame is a frame it did not open,
    and the gate says which ones instead of accepting the batch.

    A frame that already carries a verdict is not one of them. The batch reaching here is merged,
    and `merge_verdict` only carries a decision when the digest is unchanged — so such a frame is a
    picture somebody has already looked at and which has not been redrawn since. Requiring it to be
    named again would mean one rejected drawing costs a re-review of the whole set; `image-set`'s
    own note promises the opposite, and until anchors could actually be redrawn nobody could tell.
    """
    decided = accepted | rejected
    return [f.frame_id for f in frames if f.verdict == "unreviewed" and f.frame_id not in decided]


def request_markdown(
    *,
    run_dir: str,
    deliverable: str,
    contact_sheet: str,
    frames: Sequence[FrameRecord],
    consistency: ConsistencyReport,
) -> str:
    """The review request an agent reads: what to look at, what was measured, how to answer.

    Written as Markdown next to the images rather than printed, because the run that produced it
    has usually exited by the time anyone reads it, and a file survives that. Every image is
    listed with its own path — an agent opens them one at a time, and the contact sheet is for
    judging them *together*.
    """
    outliers = consistency["outliers"]
    worst = consistency["worst_pair"]
    # Only what is actually outstanding. A resumed run carries forward the verdicts on frames that
    # were not redrawn, and listing those again told a reviewer to open six pictures when three
    # were settled and unchanged.
    frames = [f for f in frames if f.verdict == "unreviewed"] or list(frames)
    lines = [
        f"# Frame review — {deliverable}",
        "",
        f"{len(frames)} image(s) are waiting on a verdict. **Open every one of them.** The",
        "measurements below cannot see whether the subject is the same subject, whether a pose is",
        "bodily possible, or whether the picture is of what was asked for — that is why you look.",
        "",
        "## Consistency across the set",
        "",
        f"- {consistency['pairs_compared']} pair(s) compared: every frame against every"
        " other, not just its neighbour",
        f"- median distance {consistency['median_distance']} "
        f"(one world measures 0.02-0.05 here; a frame from another set 0.14)",
    ]
    if worst:
        lines.append(f"- furthest apart: `{worst['a']}` and `{worst['b']}` at {worst['distance']}")
    lines.append(f"- outliers: {', '.join(f'`{o}`' for o in outliers) if outliers else 'none'}")
    lines += [
        "",
        "## The images",
        "",
        f"All together: `{contact_sheet}`",
        "",
    ]
    for i, record in enumerate(frames, 1):
        flagged = [f for f in record.findings if not f.passed]
        lines.append(f"{i}. `{record.frame_id}`")
        for finding in flagged:
            mark = "**blocker**" if finding.severity == "blocker" else "advisory"
            lines.append(f"   - {mark} `{finding.check}`: {finding.detail}")
        if not flagged:
            lines.append("   - nothing measured against it")
    accept_ids = ",".join(f.frame_id for f in frames)
    lines += [
        "",
        "## Answering",
        "",
        "Name every frame. `--accept-all` is not available to an agent: a verdict that does not",
        "mention a frame is a frame you did not open, and the gate will say which.",
        "",
        "```",
        f"content-factory frames review {run_dir} \\",
        f"  --deliverable {deliverable} --as agent \\",
        f"  --accept {accept_ids} \\",
        '  --note "what you saw"',
        "```",
        "",
        "Reject the ones that are wrong instead, with a reason that says what is wrong in the",
        "picture — those reasons are what `content-factory prompting propose` learns from:",
        "",
        "```",
        f"content-factory frames review {run_dir} --deliverable {deliverable} --as agent \\",
        '  --accept <the good ones> --reject <the bad ones> --reason "two left hands"',
        "```",
        "",
    ]
    return "\n".join(lines)
