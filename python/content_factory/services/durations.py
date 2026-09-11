"""How long a stage takes, learned from the runs that already happened.

A run's own report already records `seconds` for every stage it executed, and 240 of them were
sitting on disk carrying 1,161 timed stages before anything read them. So the estimate is not a
guess or a hand-written table: it is the median of what this machine actually did.

**Median, not mean, and the sample count travels with it.** The spread is enormous and legitimate:
`generate_anchor` has a median of 31.6 s over 149 samples and a maximum of 1,528 s, because a lane
that draws one anchor and a lane that draws six both write the same stage name. One 1,528 s
outlier would drag a mean into uselessness; the median ignores it, and `n` lets a caller say "about
a minute" versus "about a minute, from one sample".

**Keyed on (workflow, stage) first.** The same reason: `generate_anchor` on `image-set` is one
picture at ~31 s and on `audio-picture-story` it is six at ~608 s, and calling both "generate an
anchor" makes the estimate wrong for both. A workflow with no history for a stage falls back to
that stage across all workflows, and a stage never seen at all returns nothing rather than a
number somebody might believe.

Read-only and cached. Nothing here writes, and a cold read is a glob over the reports directory,
so it is done once per process and refreshed when the caller asks.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Estimate:
    """A duration in seconds, and how much evidence is behind it."""

    seconds: float
    samples: int

    @property
    def confident(self) -> bool:
        """Three runs is the point where a median stops being one run with extra steps."""
        return self.samples >= 3

    def describe(self) -> str:
        """Human phrasing, coarse on purpose — a false precision reads as a promise."""
        s = self.seconds
        if s < 90:
            rough = f"{round(s)}s"
        elif s < 3600:
            rough = f"{round(s / 60)}m"
        else:
            rough = f"{s / 3600:.1f}h"
        return rough if self.confident else f"{rough} (1 sample)" if self.samples == 1 else rough


@lru_cache(maxsize=1)
def _lane_orders() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every lane's stage order, for attributing a report written before the `workflow` field.

    A lane that cannot be loaded is skipped rather than raising: a broken YAML must cost its own
    lane's estimates, not every lane's.
    """
    from content_factory.runners.local import workflow_steps
    from content_factory.workflows.catalog import workflow_ids

    out: list[tuple[str, tuple[str, ...]]] = []
    for wf in sorted(workflow_ids()):
        try:
            out.append((wf, tuple(stage.value for _key, stage, _p in workflow_steps(wf))))
        except Exception:  # noqa: S112 - a broken lane costs its own estimates, not every lane's
            continue
    return tuple(out)


def infer_workflow(stages: Sequence[str]) -> str | None:
    """Which lane ran these stages, in this order — when exactly one lane could have.

    225 reports were on disk before `run_plan` recorded the lane it was running, representing
    weeks of GPU time, and throwing them away would have meant estimating `audio-picture-story`
    (18 stages, six anchors, half an hour) from the median of 107 `single-image` runs: it read as
    "2m left" on a run that takes thirty. A run executes a contiguous slice of its lane's stage
    order, so that sequence attributes 181 of the 225 to one lane each.

    Only ever *one* lane: a short slice like `transcribe_audio, plan_story` is the opening of
    several lanes, and guessing between them would put a picture story's numbers on an image set.
    Those 44 fall back to the all-lanes median, which is the right answer to an ambiguous question.
    """
    want = list(stages)
    if not want:
        return None
    n = len(want)
    found: str | None = None
    for lane, order in _lane_orders():
        if any(list(order[i : i + n]) == want for i in range(len(order) - n + 1)):
            if found is not None:
                return None  # ambiguous: two lanes could have produced this
            found = lane
    return found


REPORT_PATTERNS: tuple[str, ...] = (
    "*/run.json",
    "*/*/run.json",
    "*/deliverables/*/run.json",
    "*/*/deliverables/*/run.json",
    "*/*/*/deliverables/*/run.json",
)
"""Where a report can be, rather than everywhere it cannot.

`rglob("run.json")` descends into every `anchors/upscaled/raw/` and `controls/*/frames/` on the
way: **51,548 directory entries walked to find 226 files, 59 ms against 12 ms** for these five
patterns. That is on a warm page cache and after one day of runs; the media grows without bound
and the reports do not, so the gap only widens. A report somewhere these patterns do not reach is
missed, which costs an estimate a sample — the reason to bound it is that this is read on a
request path.
"""


def report_paths(root: Path) -> list[Path]:
    """Every report, found without walking the media beside it."""
    return sorted({p for pattern in REPORT_PATTERNS for p in root.glob(pattern)})


def _reports(root: Path) -> Iterable[tuple[str | None, str, float]]:
    """`(workflow, stage, seconds)` for every stage that completed, from every report on disk."""
    for path in report_paths(root):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # a half-written report during a live run is normal, not an error
        entries = [s for s in (data.get("stages") or []) if isinstance(s, dict)]
        workflow = data.get("workflow")
        if not isinstance(workflow, str) or not workflow:
            # Written before the field existed: recover the lane from what it ran.
            workflow = infer_workflow(
                [s["stage"] for s in entries if isinstance(s.get("stage"), str)]
            )
        for stage in entries:
            seconds = stage.get("seconds")
            name = stage.get("stage")
            if stage.get("ok") and isinstance(seconds, int | float) and isinstance(name, str):
                yield workflow, name, float(seconds)


Table = dict[tuple[str | None, str], Estimate]

_TABLES: dict[str, tuple[float, Table]] = {}
"""root -> (when it was built, the medians). A plain dict rather than `lru_cache` for two reasons
a request path needs: a caller can ask whether reading is free *before* paying for it, and a
rebuild can be swapped in whole. Clearing a cache and refilling it leaves a 400 ms window where
the answer is "no estimate", which on a two-second poll is a visible flicker."""

STALE_AFTER_S = 300.0
"""When a built table is old enough to be worth rebuilding. Every finished run changes it, and a
long-lived API server would otherwise answer with the history it read at startup forever."""


def _table(root: str) -> Table:
    """Medians for `(workflow, stage)` and for `(None, stage)`. Built on first use, then cached."""
    entry = _TABLES.get(root)
    if entry is not None:
        return entry[1]
    table = _build(root)
    _TABLES[root] = (time.time(), table)
    return table


def _build(root: str) -> Table:
    """One pass over every report. ~410 ms here, 376 ms of it loading the lane definitions."""
    samples: dict[tuple[str | None, str], list[float]] = {}
    for workflow, stage, seconds in _reports(Path(root)):
        # The all-workflows bucket always; the per-workflow one only when there is a workflow to
        # key on. Adding to both unconditionally counted every report from before `run_plan`
        # recorded a workflow *twice* — 149 real `generate_anchor` samples came back as 298, which
        # is how this was noticed: the number was implausible rather than merely wrong.
        samples.setdefault((None, stage), []).append(seconds)
        if workflow is not None:
            samples.setdefault((workflow, stage), []).append(seconds)
    return {
        key: Estimate(round(statistics.median(values), 1), len(values))
        for key, values in samples.items()
        if values
    }


def reports_root() -> Path:
    """Where the finished runs live. One resolver, so a caller cannot look in the wrong place."""
    return REPO_ROOT / "output"


def refresh() -> None:
    """Drop the cache. A long-lived process should call this when it wants newer history."""
    _TABLES.clear()


def is_warm(*, root: Path | None = None) -> bool:
    """Whether reading the table is now free. False means the first read will cost ~410 ms."""
    return str(root or reports_root()) in _TABLES


def is_stale(*, root: Path | None = None, max_age_s: float = STALE_AFTER_S) -> bool:
    """Whether a warm table is old enough to rebuild. False when it is not built at all: that is
    `is_warm`'s question, and answering "stale" would send a caller down the wrong path."""
    entry = _TABLES.get(str(root or reports_root()))
    return entry is not None and (time.time() - entry[0]) > max_age_s


def rebuild(*, root: Path | None = None) -> None:
    """Read the history again and swap the result in, with no window where there is no answer.

    Built first, assigned second: the old table keeps answering for the ~410 ms this takes.
    """
    key = str(root or reports_root())
    table = _build(key)
    _TABLES[key] = (time.time(), table)


def warm(*, root: Path | None = None) -> None:
    """Build the table now, so the caller that needs it next does not pay for it.

    The first read costs **~410 ms**, and only 12 ms of that is finding the reports and 5 ms is
    parsing them: **376 ms is loading the 16 lane definitions** that `infer_workflow` needs to
    attribute a report written before the lane was recorded. Once per process, and still far too
    much to spend inside a view something polls, so an async caller warms this in a thread and
    checks `is_warm` before reading.
    """
    _table(str(root or reports_root()))


def estimate_stage(
    stage: str, workflow: str | None = None, *, root: Path | None = None
) -> Estimate | None:
    """The best estimate for one stage: this workflow's own history, then the stage's, then none.

    A workflow's own median wins as soon as two runs are behind it. Below that the all-workflows
    median is the better bet — but only as a bet, so a lone same-lane sample is still returned when
    there is nothing else at all: "31 s, from one run of this lane" beats declining to answer.
    """
    table = _table(str(root or reports_root()))
    own = table.get((workflow, stage)) if workflow is not None else None
    if own is not None and own.samples >= 2:
        return own
    return table.get((None, stage)) or own


def estimate_remaining(
    stages: Iterable[str], workflow: str | None = None, *, root: Path | None = None
) -> Estimate:
    """Seconds for a list of stages still to run.

    `samples` is the *smallest* sample count behind any stage in the sum, because a total is only
    as trustworthy as its weakest term, and stages with no history at all are counted as zero and
    reported by `unknown_stages` rather than guessed at.
    """
    total = 0.0
    counts: list[int] = []
    for stage in stages:
        est = estimate_stage(stage, workflow, root=root)
        if est is None:
            continue
        total += est.seconds
        counts.append(est.samples)
    return Estimate(round(total, 1), min(counts) if counts else 0)


def unknown_stages(
    stages: Iterable[str], workflow: str | None = None, *, root: Path | None = None
) -> list[str]:
    """Stages the history cannot speak for, so a caller can say so instead of under-promising."""
    return [s for s in stages if estimate_stage(s, workflow, root=root) is None]


@dataclass(frozen=True)
class Forecast:
    """When the work that is left will be finished.

    `remaining_seconds` counts the stages not yet started plus whatever is left of the one in
    flight. `samples` is the weakest term's evidence, `unknown` names the stages history cannot
    speak for, and `overdue` says the running stage has already outlasted its own median — which
    is the honest reading of "0 seconds left" and stops the UI promising a finish that has passed.
    """

    remaining_seconds: float
    samples: int
    unknown: tuple[str, ...]
    overdue: bool

    @property
    def confident(self) -> bool:
        return self.samples >= 3 and not self.unknown

    def describe(self) -> str:
        """ "4m left", and the caveats where they exist rather than in a tooltip nobody opens."""
        rough = Estimate(self.remaining_seconds, self.samples).describe()
        if self.overdue:
            return f"{rough} left (over its usual time)"
        if self.unknown:
            return f"{rough} left, plus {len(self.unknown)} stage(s) never timed"
        return f"{rough} left"

    def finish_at(self, now: datetime | None = None) -> datetime:
        """The clock time to put on screen. UTC and aware, so the browser localises it."""
        return (now or datetime.now(UTC)) + timedelta(seconds=self.remaining_seconds)


def forecast(
    remaining: Iterable[str],
    workflow: str | None = None,
    *,
    running: tuple[str, float] | None = None,
    root: Path | None = None,
) -> Forecast:
    """Seconds until a run in progress is done.

    `running` is `(stage, seconds it has been running)` and is charged only for the part of its
    median it has not used up, floored at zero: a stage that has already run twice its median has
    nothing left to promise, and saying "-40s" or silently re-adding the whole median would both
    be worse than saying it is overdue.
    """
    stages = list(remaining)
    total = estimate_remaining(stages, workflow, root=root)
    seconds = total.seconds
    counts = [total.samples] if total.samples else []
    unknown = list(unknown_stages(stages, workflow, root=root))
    overdue = False
    if running is not None:
        stage, elapsed = running
        est = estimate_stage(stage, workflow, root=root)
        if est is None:
            unknown.insert(0, stage)
        else:
            left = est.seconds - elapsed
            overdue = left <= 0
            seconds += max(left, 0.0)
            counts.append(est.samples)
    return Forecast(
        remaining_seconds=round(seconds, 1),
        samples=min(counts) if counts else 0,
        unknown=tuple(unknown),
        overdue=overdue,
    )
