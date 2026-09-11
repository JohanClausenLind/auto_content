"""Every run this machine has made, and what came out of it.

A run leaves everything it produced on disk under ``output/<project>/`` — the film, the narration,
the anchor drawings, the control maps, the captions — and a ``run.json`` saying what it did. There
were **225 of those reports and 34 films** sitting there with no way to look at any of it except
by knowing the path: the web app's run list reads the ``production_runs`` table, and a local run
(``content-factory make``) never writes a row to it. So the day's actual work was invisible to the
product that made it.

This module is the reader. It is deliberately not a new source of truth:

* **The packages manifest wins.** A run that reached ``compile_destination_packages`` wrote
  ``destination-packages/packages.json`` naming every deliverable file with its role, byte count
  and content type. Where that exists it *is* the answer, because it is what the run itself
  decided it had produced.
* **A run that never got that far still has outputs**, and they are the ones most worth seeing:
  most runs here block at ``review_frames`` — six of seven overnight image sets did — with the
  drawings on disk and no package. Those are found by scanning, and the path says what each file
  is (``anchors/`` a drawing, ``controls/`` a debug map, ``exports/`` a film).
* **Nothing is written, moved or deleted.** Read-only, so it cannot cost a run anything.

Kept out of ``services/runs.py`` on purpose: that module is the durable Temporal runs, keyed by
database row and workspace. These are local runs keyed by a directory, and the two have different
identities, different lifetimes and no join. See also :mod:`content_factory.services.durations`,
which reads the same reports for their timings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from content_factory.services import frame_reviews
from content_factory.services.durations import infer_workflow

REPO_ROOT = Path(__file__).resolve().parents[3]

Kind = Literal["image", "video", "audio", "text", "data"]
Outcome = Literal["complete", "review", "blocked", "stopped", "failed"]
"""The five states a run ends in. `review` and `blocked` both mean "a person has to look", and
they are kept apart because a gate is a planned stop and a block is a stage that gave up."""

MEDIA_TYPES: dict[str, tuple[Kind, str]] = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
    ".gif": ("image", "image/gif"),
    ".mp4": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"),
    ".wav": ("audio", "audio/wav"),
    ".mp3": ("audio", "audio/mpeg"),
    ".m4a": ("audio", "audio/mp4"),
    ".flac": ("audio", "audio/flac"),
    ".ogg": ("audio", "audio/ogg"),
    ".txt": ("text", "text/plain"),
    ".srt": ("text", "text/plain"),
    ".vtt": ("text", "text/vtt"),
    ".ass": ("text", "text/plain"),
    ".md": ("text", "text/markdown"),
    ".json": ("data", "application/json"),
    ".pdf": ("data", "application/pdf"),
}
"""What may be served, and as what. An allowlist rather than a denylist: this maps directly onto
an HTTP route, and "everything except the dangerous ones" is a list nobody finishes."""

# Where a file sits says what it is, for the runs that never wrote a manifest. Longest match wins,
# so `anchors/upscaled/` is not read as plain `anchors/`.
_ROLE_BY_PATH: tuple[tuple[str, str], ...] = (
    ("exports/", "video"),
    ("anchors/upscaled/", "anchor-upscaled"),
    ("anchors/", "anchor"),
    ("sequence/", "frame"),
    ("frames/", "frame"),
    ("controls/", "control"),
    ("audio/", "audio"),
    ("captions/", "caption"),
    ("uploads/", "input"),
    ("artifacts/", "render"),
)

# Files the runner writes so a stage can be re-run safely, not files a run produced. Measured on
# a finished picture story: **85 of its 125 JSON files** were `<frame>.done.json` markers, which
# would have made the "text and data" list of a six-picture story 130 rows of bookkeeping.
_MARKER_NAMES = frozenset({"manifest.json", "chain.json", "job.json", "bundle.json"})


def _is_marker(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return name.endswith(".done.json") or name in _MARKER_NAMES


# Debug and intermediate output. Kept and labelled rather than hidden — a control map is exactly
# what you want when a drawing came out wrong — but the UI can fold it away, and it must never
# outrank the film when picking one image to show.
DEBUG_ROLES = frozenset({"control", "anchor-upscaled", "render", "input", "marker"})

MAX_OUTPUTS = 400
"""A run with a thousand frames should not answer with a thousand rows. The count is reported
separately, so a truncated list says so instead of quietly looking complete."""


@dataclass(frozen=True)
class RunOutput:
    """One file a run produced."""

    path: str
    """Relative to the run's directory, so it is both the id and the thing to fetch."""
    kind: Kind
    role: str
    bytes: int
    content_type: str


@dataclass(frozen=True)
class RunRecord:
    """One run, as a history row."""

    run_id: str
    workflow: str | None
    outcome: Outcome
    finished_at: float
    """Epoch seconds, from the report's own mtime — when the run last wrote anything."""
    seconds: float
    """What it actually cost, summed from the stages it timed. The evidence behind every ETA."""
    stages: int
    stages_ok: int
    blocked_at: str | None
    project_dir: str
    deliverable_id: str | None
    outputs: list[RunOutput] = field(default_factory=list)
    outputs_total: int = 0
    """How many files there are, which is not `len(outputs)` once `MAX_OUTPUTS` truncates."""
    awaiting_review: int = 0
    """Drawings this run is waiting on somebody to look at.

    Carried on every row, including the list, because "which of these 226 runs wants me" is the
    question the history is opened with. It is not the same as `outcome == "review"`: a run whose
    frames were all rejected is also parked at the gate, and what it needs is a redraw, not a
    reviewer."""

    @property
    def film(self) -> str | None:
        """The finished film, if there is one. `exports/final.mp4` is the run's own answer to
        "which of these videos is the deliverable"; anything else is an intermediate."""
        videos = [o for o in self.outputs if o.kind == "video"]
        if not videos:
            return None
        final = next((o for o in videos if o.path.endswith("exports/final.mp4")), None)
        return (final or videos[0]).path

    @property
    def poster(self) -> str | None:
        """One image to represent the run. Never a control map or an upscale intermediate: those
        are debug output, and a history row showing a pose skeleton is a row nobody recognises."""
        images = [o for o in self.outputs if o.kind == "image" and o.role not in DEBUG_ROLES]
        return images[0].path if images else None

    def as_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "outputs"},
            "outputs": [asdict(o) for o in self.outputs],
            "film": self.film,
            "poster": self.poster,
        }


def history_root() -> Path:
    """Where runs live. Anchored to the repo, not the process's working directory: the API is
    started from wherever, and a relative root would make the history depend on that."""
    return REPO_ROOT / "output"


def encode_run_id(run_dir: Path, root: Path) -> str:
    """A run's id is its directory, with ``/`` written ``~``.

    Readable on purpose — ``overnight~ps1c-pinecone`` is greppable and says which run it is, where
    an opaque hash would need a lookup table to debug. ``~`` because it survives a URL path
    segment unescaped and cannot appear in the directory names the runner makes.
    """
    return run_dir.resolve().relative_to(root.resolve()).as_posix().replace("/", "~")


def decode_run_id(run_id: str, root: Path) -> Path | None:
    """The directory an id names, or None when it names something outside the output root.

    Both halves of the check matter: ``~`` cannot smuggle a ``..`` past `relative_to`, but a
    *symlink* inside the output tree could point anywhere, and only resolving both sides catches
    that.
    """
    if not run_id or run_id.startswith(("~", ".")) or "/" in run_id or "\\" in run_id:
        return None
    root = root.resolve()
    candidate = (root / run_id.replace("~", "/")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_dir():
        return None
    return candidate


def resolve_output(run_dir: Path, relative: str) -> Path | None:
    """One output file, checked. None unless it stays inside the run and is a servable type."""
    if not relative or relative.startswith("/"):
        return None
    run_dir = run_dir.resolve()
    target = (run_dir / relative).resolve()
    if not target.is_relative_to(run_dir) or not target.is_file():
        return None
    if target.suffix.lower() not in MEDIA_TYPES:
        return None
    return target


def _role_for(relative: str) -> str:
    if _is_marker(relative):
        return "marker"
    best = ""
    role = "other"
    for prefix, name in _ROLE_BY_PATH:
        if prefix in relative and len(prefix) > len(best):
            best, role = prefix, name
    return role


def _from_manifest(deliverable_dir: Path) -> list[RunOutput]:
    """The run's own list of what it produced, from ``destination-packages/packages.json``."""
    manifest = deliverable_dir / "destination-packages" / "packages.json"
    try:
        packages = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(packages, list):
        return []
    out: list[RunOutput] = []
    seen: set[str] = set()
    prefix = deliverable_dir.name
    for package in packages:
        for entry in (package or {}).get("files") or []:
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            # Manifest paths are relative to the deliverable; the run directory is the id, so
            # they are re-based here rather than making the caller know which is which.
            relative = f"deliverables/{prefix}/{path}"
            if relative in seen:
                continue
            kind_type = MEDIA_TYPES.get(Path(path).suffix.lower())
            if kind_type is None:
                continue
            seen.add(relative)
            out.append(
                RunOutput(
                    path=relative,
                    kind=kind_type[0],
                    role=str(entry.get("role") or _role_for(relative)),
                    bytes=int(entry.get("bytes") or 0),
                    content_type=str(entry.get("content_type") or kind_type[1]),
                )
            )
    return out


def _by_scan(run_dir: Path) -> list[RunOutput]:
    """Everything servable under the run, for a run that never wrote a manifest."""
    out: list[RunOutput] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        kind_type = MEDIA_TYPES.get(path.suffix.lower())
        if kind_type is None:
            continue
        relative = path.relative_to(run_dir).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        out.append(
            RunOutput(
                path=relative,
                kind=kind_type[0],
                role=_role_for(relative),
                bytes=size,
                content_type=kind_type[1],
            )
        )
    return out


def _rank(output: RunOutput) -> tuple[int, int, str]:
    """Films first, then real drawings, then everything, then debug. The order the UI shows."""
    kind_rank = {"video": 0, "image": 1, "audio": 2, "text": 3, "data": 4}
    return (1 if output.role in DEBUG_ROLES else 0, kind_rank.get(output.kind, 5), output.path)


def run_outputs(run_dir: Path, *, limit: int = MAX_OUTPUTS) -> tuple[list[RunOutput], int]:
    """What a run produced, and how many files that is before truncation.

    The manifest is used where it exists **and** the scan is still run, because a manifest lists
    the *deliverable* — the film, the narration, the captions — and says nothing about the six
    anchor drawings, which for a run blocked at review are the only thing there is to look at.
    """
    found: dict[str, RunOutput] = {}
    for deliverable in sorted((run_dir / "deliverables").glob("*")):
        if deliverable.is_dir():
            for entry in _from_manifest(deliverable):
                found[entry.path] = entry
    for entry in _by_scan(run_dir):
        found.setdefault(entry.path, entry)  # the manifest's own account wins on a collision
    ordered = sorted(found.values(), key=_rank)
    return ordered[:limit], len(ordered)


def _outcome(report: dict, stages: list[dict]) -> Outcome:
    """What happened, in the four words that want four different responses.

    A human review gate records neither `blocked` nor `blocked_at` — it raises like any other
    stage failure — so without this check the six picture stories and six image sets parked at
    `review_frames` with their drawings finished all read as **failed**. They are waiting for
    somebody to look, which is the opposite of a defect.
    """
    if report.get("passed"):
        return "complete"
    if report.get("stopped"):
        return "stopped"
    if report.get("blocked_at"):
        return "blocked"
    last = stages[-1] if stages else None
    if last is not None and not last.get("ok"):
        from content_factory.deliverables.dag_compiler import human_gate_stages

        if last.get("stage") in human_gate_stages():
            return "review"
    return "failed"


def _record(report_path: Path, root: Path, *, with_outputs: bool) -> RunRecord | None:
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        return None  # a half-written report during a live run is normal, not an error
    if not isinstance(report, dict) or "stages" not in report:
        return None
    stages = [s for s in (report.get("stages") or []) if isinstance(s, dict)]
    # `deliverables/<id>/run.json` -> the run is the directory two levels up. A report written
    # anywhere else is taken at face value: its own directory is the run.
    run_dir = report_path.parent
    if run_dir.parent.name == "deliverables":
        run_dir = run_dir.parent.parent
    try:
        run_id = encode_run_id(run_dir, root)
    except ValueError:
        return None
    workflow = report.get("workflow")
    if not isinstance(workflow, str) or not workflow:
        workflow = infer_workflow([s["stage"] for s in stages if isinstance(s.get("stage"), str)])
    outputs: list[RunOutput] = []
    total = 0
    if with_outputs:
        outputs, total = run_outputs(run_dir)
    return RunRecord(
        run_id=run_id,
        workflow=workflow,
        outcome=_outcome(report, stages),
        finished_at=report_path.stat().st_mtime,
        seconds=round(sum(float(s.get("seconds") or 0.0) for s in stages), 1),
        stages=len(stages),
        stages_ok=sum(1 for s in stages if s.get("ok")),
        blocked_at=report.get("blocked_at") if isinstance(report.get("blocked_at"), str) else None,
        project_dir=str(report.get("project_dir") or run_dir),
        deliverable_id=(
            report.get("deliverable_id") if isinstance(report.get("deliverable_id"), str) else None
        ),
        outputs=outputs,
        outputs_total=total,
        awaiting_review=frame_reviews.waiting_frames(run_dir),
    )


def list_runs(*, root: Path | None = None, limit: int = 100) -> list[RunRecord]:
    """Every run on disk, newest first. Cheap: no output scanning, one stat per report.

    Scanning outputs for every run would mean walking the whole output tree — 213 directories and
    tens of thousands of files — to draw a list. The list carries counts and the detail view pays
    for the files of the one run somebody actually opened.
    """
    root = (root or history_root()).resolve()
    if not root.is_dir():
        return []
    records = [
        record
        for path in root.rglob("run.json")
        if (record := _record(path, root, with_outputs=False)) is not None
    ]
    records.sort(key=lambda r: r.finished_at, reverse=True)
    return records[:limit]


def get_run(run_id: str, *, root: Path | None = None) -> RunRecord | None:
    """One run with its outputs, or None when the id names nothing inside the output root."""
    root = (root or history_root()).resolve()
    run_dir = decode_run_id(run_id, root)
    if run_dir is None:
        return None
    reports = [
        *sorted(run_dir.glob("deliverables/*/run.json")),
        *([run_dir / "run.json"] if (run_dir / "run.json").is_file() else []),
    ]
    for report_path in reports:
        record = _record(report_path, root, with_outputs=True)
        if record is not None:
            return record
    return None
