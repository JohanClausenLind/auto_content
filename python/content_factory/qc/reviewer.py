"""Who reviews this run's pictures — and, when it is an agent, what it has to do to say yes."""

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
    """Who should review this run: an explicit choice, then the environment, then a person."""
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
    """Frames an agent's verdict did not name and nobody has decided yet."""
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
    """The review request an agent reads: what to look at, what was measured, how to answer."""
    outliers = consistency["outliers"]
    worst = consistency["worst_pair"]
    # Only what is outstanding: a resumed run carries forward verdicts on frames not redrawn, and
    # listing those again told a reviewer to open six pictures when three were settled.
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
