"""Pinned nodes: freeze a step's output so a rerun does not pay for it again.

``--from`` already lets a run resume at a node, and that is the right instrument for "carry on
from where it stopped". It is the wrong one for the loop this pipeline is actually iterated in:
the expensive nodes are near the *front* — ``generate_anchor``, ``synthesize_narration``,
``generate_video`` — and the node being worked on is near the back. Reaching it with ``--from``
means naming it every single time and trusting that nothing before it mattered, and reaching it
with a full run means paying minutes of GPU time to regenerate pictures nobody is looking at.

A pin says the other thing: *this node's output is frozen; never execute it, whatever else runs.*
Pin the three costly nodes once and the tail can be run flat out, repeatedly, with one command.

Two rules keep a pin honest rather than a way to lie about a run:

- **A pin is only valid while its output is on disk.** The stage wrote files that later stages
  read; if the run directory has been cleared, the pin is stale and the run refuses rather than
  executing a lane whose middle is missing. :func:`validate` is what says so.
- **A pinned stage is recorded as pinned.** It lands in ``run.json`` with ``pinned: true`` and
  ``ok: true``, so provenance reads "this output was frozen, not produced by this run", and
  `services/durations.py` leaves it out of the medians — a stage that was skipped took no time,
  and letting a 0.0 s sample into the history would tell every future run that a six-minute
  generation is instant.

Pins live beside the report they describe, in the run directory, so they travel with the run and
clearing the run clears them.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PINS_FILENAME = "pins.json"


class PinError(ValueError):
    """A pin was asked for that cannot be honoured. The message always names the node."""


@dataclass(frozen=True)
class Pin:
    """One frozen node.

    ``outputs_hash`` is the hash the stage reported when it last ran, kept so a report can say
    *which* output is being reused rather than only that one is.
    """

    node: str
    stage: str
    outputs_hash: str
    facts: dict[str, Any]
    pinned_at: float

    def describe(self) -> str:
        age = max(0.0, time.time() - self.pinned_at)
        if age < 90:
            when = f"{int(age)}s ago"
        elif age < 5400:
            when = f"{int(age // 60)}m ago"
        else:
            when = f"{age / 3600:.1f}h ago"
        return f"{self.node} ({self.stage}, {self.outputs_hash[:12]}, pinned {when})"


def pins_path(run_dir: Path) -> Path:
    return run_dir / PINS_FILENAME


def load(run_dir: Path) -> dict[str, Pin]:
    """Every pin recorded for this run directory, by node key.

    A malformed or unreadable file reads as "no pins" rather than raising: pins are an
    optimisation, and a run must never fail to start because of one.
    """
    path = pins_path(run_dir)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Pin] = {}
    for node, entry in (raw.get("pins") or {}).items():
        if not isinstance(node, str) or not isinstance(entry, dict):
            continue
        stage = entry.get("stage")
        outputs_hash = entry.get("outputs_hash")
        if not isinstance(stage, str) or not isinstance(outputs_hash, str):
            continue
        facts = entry.get("facts")
        out[node] = Pin(
            node=node,
            stage=stage,
            outputs_hash=outputs_hash,
            facts=facts if isinstance(facts, dict) else {},
            pinned_at=float(entry.get("pinned_at") or 0.0),
        )
    return out


def save(run_dir: Path, pins: dict[str, Pin]) -> Path:
    """Write the pin file atomically; remove it when nothing is pinned."""
    path = pins_path(run_dir)
    if not pins:
        path.unlink(missing_ok=True)
        return path
    doc = {
        "$comment": (
            "Nodes frozen for this run: the runner skips them and leaves the output already on "
            "disk in place. Written by `content-factory pins`. Delete this file to unpin "
            "everything."
        ),
        "pins": {
            node: {
                "stage": pin.stage,
                "outputs_hash": pin.outputs_hash,
                "facts": pin.facts,
                "pinned_at": pin.pinned_at,
            }
            for node, pin in sorted(pins.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(path)
    return path


def completed_nodes(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The nodes a report says finished, by node key, latest record winning.

    A node can appear twice when a run was resumed; the last one is the state on disk.
    """
    out: dict[str, dict[str, Any]] = {}
    for entry in report.get("stages") or []:
        if not isinstance(entry, dict) or not entry.get("ok"):
            continue
        node = entry.get("node") or entry.get("stage")
        if isinstance(node, str):
            out[node] = entry
    return out


def pin_nodes(run_dir: Path, report: dict[str, Any], nodes: list[str]) -> dict[str, Pin]:
    """Freeze ``nodes`` using what the last run recorded for them.

    A node this run never completed cannot be pinned: there would be no output to reuse, and a pin
    that quietly means "skip and hope" is the failure mode this whole module exists to avoid.
    """
    done = completed_nodes(report)
    pins = load(run_dir)
    unknown = [n for n in nodes if n not in done]
    if unknown:
        msg = (
            f"cannot pin {', '.join(sorted(unknown))}: no completed output recorded in "
            f"{run_dir / 'run.json'}. Run the node once, then pin it."
        )
        raise PinError(msg)
    now = time.time()
    for node in nodes:
        entry = done[node]
        facts = entry.get("facts")
        pins[node] = Pin(
            node=node,
            stage=str(entry.get("stage") or node),
            outputs_hash=str(entry.get("outputs_hash") or ""),
            facts=dict(facts) if isinstance(facts, dict) else {},
            pinned_at=now,
        )
    save(run_dir, pins)
    return pins


def unpin_nodes(run_dir: Path, nodes: list[str]) -> dict[str, Pin]:
    """Release ``nodes``. Unpinning something that is not pinned is not an error."""
    pins = load(run_dir)
    for node in nodes:
        pins.pop(node, None)
    save(run_dir, pins)
    return pins


def validate(run_dir: Path, pins: dict[str, Pin], node_keys: list[str]) -> list[str]:
    """Problems with the pins about to be honoured, as plain sentences.

    Checked here rather than at execution time so a run refuses before it spends anything, not
    six minutes in when a later stage cannot find what it needed.
    """
    problems: list[str] = []
    known = set(node_keys)
    for node, pin in sorted(pins.items()):
        if node not in known:
            problems.append(
                f"{node} is pinned but this workflow has no such node — "
                f"`content-factory pins clear` if the lane changed under it"
            )
            continue
        if not pin.outputs_hash:
            problems.append(f"{node} is pinned with no recorded output hash")
    if not pins_path(run_dir).parent.is_dir() and pins:
        problems.append(f"{run_dir} does not exist, so no pinned output is on disk")
    return problems
