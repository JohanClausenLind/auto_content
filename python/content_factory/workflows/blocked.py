"""BLOCKED: a run that stopped because the machine could not do it, not because it broke.

There is a real difference between the two and the pipeline had one word for both. A stage that
raises `RuntimeError` is a failure: something is wrong, retrying it is reasonable, and the operator
is being asked to fix code or configuration. A stage that has generated a frame three times, run
the deterministic checks each time and been handed three unusable pictures is not broken — it has
done its job and the answer is "a person has to look at this". Retrying it burns GPU hours to
produce a fourth unusable picture.

`RunState.BLOCKED` has existed in `db/models.py` since the run state machine was written and
nothing ever set it (STATUS 2992). This is what sets it. A `BlockedError` carries three things a
person needs and an exception message cannot hold: **why**, **how many attempts** were spent, and
**which candidate images** are on disk to look at. It converts to a non-retryable Temporal outcome
on the durable path, to `{"ok": false, "blocked": {...}}` in a local run report, and to exit code 5
with a `BLOCK` label at the CLI — deliberately not the failure code, so a wrapper script can tell
"needs a human" from "is broken" without parsing text.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

BLOCKED_EXIT_CODE = 5
"""Distinct from 1 (usage), 2 (bad arguments), 3 (refused by preflight). A caller that sees 5 knows
the run stopped on judgement rather than on a defect."""

BLOCKED_FAILURE_TYPE = "Blocked"
"""The ``ApplicationError`` type the durable path tags, so workflow code can recognise it without
importing the stage module into the Temporal sandbox."""


class BlockedError(RuntimeError):
    """A stage that cannot proceed without a person, with everything that person needs."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "",
        attempts: int = 0,
        candidates: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.attempts = attempts
        # Each entry names an image on disk and why it was rejected. Preserved rather than
        # deleted: the whole point is that someone looks at the attempts and decides.
        self.candidates: tuple[dict[str, Any], ...] = tuple(candidates or ())

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "stage": self.stage,
            "attempts": self.attempts,
            "candidates": [dict(c) for c in self.candidates],
        }

    def __str__(self) -> str:
        tail = f" after {self.attempts} attempt(s)" if self.attempts else ""
        paths = ", ".join(str(c.get("path")) for c in self.candidates if c.get("path"))
        look = f"; look at {paths}" if paths else ""
        return f"{self.reason}{tail}{look}"


def blocked_details(exc: BaseException | None) -> dict[str, Any] | None:
    """The blocked payload carried by ``exc`` or anything it wraps, else None.

    Both directions are covered. Locally the exception *is* a ``BlockedError`` (possibly wrapped by
    ``LocalRunError``); on the durable path it arrives as an ``ActivityError`` around an
    ``ApplicationError`` whose type is :data:`BLOCKED_FAILURE_TYPE` and whose first detail is the
    payload. Walking `__cause__` covers both without the workflow code needing to know which.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BlockedError):
            return exc.as_dict()
        payload = getattr(exc, "blocked", None)
        if isinstance(payload, dict):
            return payload
        if getattr(exc, "type", None) == BLOCKED_FAILURE_TYPE:
            details = getattr(exc, "details", ()) or ()
            if details and isinstance(details[0], dict):
                return details[0]
            return {"reason": str(exc), "attempts": 0, "candidates": []}
        exc = exc.__cause__ or getattr(exc, "cause", None)
    return None
