"""BLOCKED: a run that stopped because the machine could not do it, not because it broke."""

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
    """The blocked payload carried by ``exc`` or anything it wraps, else None."""
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
