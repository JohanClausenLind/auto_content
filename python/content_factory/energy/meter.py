"""Energy integration and allocation.

Two measurement paths: a reliable cumulative counter is integrated by differences (counter
resets detected and handled — a decrease means the meter restarted, and only post-reset energy
is counted for that segment); power-only sampling is integrated by trapezoid over elapsed time,
and any sampling hole longer than ``max_gap_s`` becomes a recorded gap with NO energy counted —
missing data stays visible instead of becoming invented watt-hours.

Attribution follows a documented policy: within each interval between job boundaries, measured
energy is split evenly among the jobs active in that interval; intervals with no active job go
to the explicit ``idle`` bucket. Shares always sum to the measured total.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise

from content_factory.schemas.energy import EnergyShare, PowerSample, Tariff

IDLE_KEY = "idle"


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def integrate_power(
    samples: Sequence[PowerSample], *, max_gap_s: float = 120.0
) -> tuple[float, tuple[tuple[str, str], ...]]:
    """Trapezoid integration of watt samples → (kWh, gaps). Gaps carry zero measured energy."""
    ordered = sorted(samples, key=lambda s: s.at)
    kwh = 0.0
    gaps: list[tuple[str, str]] = []
    for a, b in pairwise(ordered):
        dt_s = (_ts(b.at) - _ts(a.at)).total_seconds()
        if dt_s <= 0:
            continue
        if dt_s > max_gap_s:
            gaps.append((a.at, b.at))
            continue
        kwh += (a.watts + b.watts) / 2 * dt_s / 3_600_000
    return round(kwh, 9), tuple(gaps)


def integrate_counter(samples: Sequence[PowerSample]) -> float:
    """Difference a cumulative Wh counter; a decrease is a meter reset, not negative energy."""
    ordered = [s for s in sorted(samples, key=lambda s: s.at) if s.cumulative_wh is not None]
    wh = 0.0
    for a, b in pairwise(ordered):
        assert a.cumulative_wh is not None and b.cumulative_wh is not None
        delta = b.cumulative_wh - a.cumulative_wh
        wh += b.cumulative_wh if delta < 0 else delta  # reset: count post-reset accumulation
    return round(wh / 1000, 9)


def attribute_energy(
    kwh_total: float,
    window_start: str,
    window_end: str,
    jobs: Sequence[tuple[str, str, str]],  # (job_id, start_iso, end_iso)
) -> tuple[EnergyShare, ...]:
    """Split measured energy across job intervals and an explicit idle bucket; sums exactly."""
    t0, t1 = _ts(window_start), _ts(window_end)
    total_s = (t1 - t0).total_seconds()
    if total_s <= 0:
        msg = "attribution window must have positive duration"
        raise ValueError(msg)
    bounds = {t0, t1}
    clipped: list[tuple[str, datetime, datetime]] = []
    for job_id, s, e in jobs:
        js, je = max(_ts(s), t0), min(_ts(e), t1)
        if je > js:
            clipped.append((job_id, js, je))
            bounds.update((js, je))
    cut = sorted(bounds)
    shares: dict[str, float] = {}
    for a, b in pairwise(cut):
        seg_kwh = kwh_total * (b - a).total_seconds() / total_s
        active = [j for j, js, je in clipped if js <= a and je >= b]
        if active:
            for j in active:
                shares[j] = shares.get(j, 0.0) + seg_kwh / len(active)
        else:
            shares[IDLE_KEY] = shares.get(IDLE_KEY, 0.0) + seg_kwh
    # Absorb float residue into the largest bucket so the report reconciles exactly.
    residue = kwh_total - sum(shares.values())
    if shares and abs(residue) > 0:
        biggest = max(shares, key=lambda k: shares[k])
        shares[biggest] += residue
    return tuple(EnergyShare(key=k, kwh=round(v, 9)) for k, v in sorted(shares.items()))


def energy_cost(kwh: float, tariff: Tariff) -> float:
    """kWh times the all-in price. Cloud rentals already include power — never charge a
    second time; any cloud energy estimate is a separate physical metric, not a cost."""
    return round(kwh * tariff.price_per_kwh, 6)
