"""Cost ledger (18.7): estimate → reserve → settle against operator caps. Spend safety only —
no plans, credits-as-product, or invoices. UsageEvents are immutable and append-only."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from content_factory.db.base import new_id


class BudgetError(Exception):
    pass


class BudgetExceededError(BudgetError):
    """Raised on reservation that would break a hard cap. Never raised on settlement of an
    already-reserved amount (the reservation bounded it)."""


class Scope(StrEnum):
    monthly = "monthly"
    project = "project"
    program = "program"
    skill = "skill"


@dataclass(frozen=True)
class Cap:
    scope: Scope
    key: str  # e.g. "2026-09" | project_id | program_id | skill_id
    limit_usd: float
    warn_ratio: float = 0.7
    hard_stop: bool = True


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    caps: tuple[Cap, ...]
    estimated_usd: float
    purpose: str
    created_at: str


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    reservation_id: str
    actual_usd: float
    provider: str
    detail: str
    local_gpu_seconds: float
    created_at: str


@dataclass
class _Bucket:
    reserved: float = 0.0
    settled: float = 0.0

    @property
    def committed(self) -> float:
        return self.reserved + self.settled


@dataclass
class CostLedger:
    """In-memory engine (DB persistence arrives with the durable pipeline). Thread-safe."""

    caps: dict[tuple[Scope, str], Cap] = field(default_factory=dict)
    _buckets: dict[tuple[Scope, str], _Bucket] = field(default_factory=dict)
    _open: dict[str, Reservation] = field(default_factory=dict)
    events: list[UsageEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_cap(self, cap: Cap) -> None:
        with self._lock:
            self.caps[(cap.scope, cap.key)] = cap
            self._buckets.setdefault((cap.scope, cap.key), _Bucket())

    def headroom(self, scope: Scope, key: str) -> float:
        with self._lock:
            cap = self.caps.get((scope, key))
            if cap is None:
                return float("inf")
            b = self._buckets.setdefault((scope, key), _Bucket())
            return cap.limit_usd - b.committed

    def reserve(
        self, estimated_usd: float, *, scopes: list[tuple[Scope, str]], purpose: str
    ) -> Reservation:
        if estimated_usd < 0:
            raise BudgetError("negative estimate")
        with self._lock:
            caps: list[Cap] = []
            for scope, key in scopes:
                cap = self.caps.get((scope, key))
                if cap is None:
                    continue
                b = self._buckets.setdefault((scope, key), _Bucket())
                if cap.hard_stop and b.committed + estimated_usd > cap.limit_usd + 1e-9:
                    raise BudgetExceededError(
                        f"{scope.value}:{key} cap ${cap.limit_usd:.2f} would be exceeded "
                        f"(committed ${b.committed:.2f} + estimate ${estimated_usd:.2f})"
                    )
                caps.append(cap)
            res = Reservation(
                reservation_id=new_id("rsv"),
                caps=tuple(caps),
                estimated_usd=estimated_usd,
                purpose=purpose,
                created_at=datetime.now(UTC).isoformat(),
            )
            for cap in caps:
                b = self._buckets[(cap.scope, cap.key)]
                b.reserved += estimated_usd
                if b.committed >= cap.limit_usd * cap.warn_ratio:
                    self.warnings.append(
                        f"{cap.scope.value}:{cap.key} at {b.committed / cap.limit_usd:.0%} of ${cap.limit_usd:.2f}"  # noqa: E501
                    )
            self._open[res.reservation_id] = res
            return res

    def settle(
        self,
        reservation: Reservation,
        actual_usd: float,
        *,
        provider: str,
        detail: str = "",
        local_gpu_seconds: float = 0.0,
    ) -> UsageEvent:
        if actual_usd < 0:
            raise BudgetError("negative actuals")
        with self._lock:
            if reservation.reservation_id not in self._open:
                raise BudgetError("reservation unknown or already settled")
            del self._open[reservation.reservation_id]
            for cap in reservation.caps:
                b = self._buckets[(cap.scope, cap.key)]
                b.reserved -= reservation.estimated_usd
                b.settled += actual_usd
            event = UsageEvent(
                event_id=new_id("use"),
                reservation_id=reservation.reservation_id,
                actual_usd=actual_usd,
                provider=provider,
                detail=detail,
                local_gpu_seconds=local_gpu_seconds,
                created_at=datetime.now(UTC).isoformat(),
            )
            self.events.append(event)
            return event

    def release(self, reservation: Reservation) -> None:
        """Cancelled work releases its reservation without a usage event."""
        with self._lock:
            if reservation.reservation_id not in self._open:
                return
            del self._open[reservation.reservation_id]
            for cap in reservation.caps:
                self._buckets[(cap.scope, cap.key)].reserved -= reservation.estimated_usd

    def spent(self, scope: Scope, key: str) -> float:
        with self._lock:
            return self._buckets.setdefault((scope, key), _Bucket()).settled
