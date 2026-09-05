"""Forecasting baselines and the adapter roster.

Forecasts are DISPLAY-ONLY inputs: they are shown next to real measurements and never override
a hard spend limit, memory limit, license restriction, or current allocation fact. Foundation
models (TimesFM 3.0 — weight license allows specified noncommercial/nonproduction uses as noted
by the operator on 2026-09-05, re-verify at adoption; TimesFM 2.5; Chronos-2 — model card claims
Apache-2.0, verify the exact artifact) are optional adapters to be added with pinned revisions
from a registry/research pass, never from memory. Every candidate must beat these baselines in
a rolling-origin backtest before it earns a place in any scheduling loop.

Metrics: WAPE (Σ|error| / Σ|actual|) and MAE — chosen because sparse, zero-containing series
make MAPE meaningless.
"""

from __future__ import annotations

from collections.abc import Sequence


def available_adapters() -> tuple[str, ...]:
    """Adapters usable right now, offline, with no weight downloads."""
    return ("seasonal_naive", "rolling_mean")


def seasonal_naive_forecast(history: Sequence[float], *, season: int, horizon: int) -> list[float]:
    """Repeat the last observed season. The standard honest baseline for periodic series."""
    if season < 1 or len(history) < season:
        msg = f"seasonal naive needs at least one full season ({season}), got {len(history)}"
        raise ValueError(msg)
    last_season = list(history[-season:])
    return [last_season[i % season] for i in range(horizon)]


def rolling_mean_forecast(history: Sequence[float], *, window: int, horizon: int) -> list[float]:
    if window < 1 or len(history) < window:
        msg = f"rolling mean needs {window} observations, got {len(history)}"
        raise ValueError(msg)
    level = sum(history[-window:]) / window
    return [level] * horizon


def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        msg = "MAE needs equal-length, non-empty series"
        raise ValueError(msg)
    return sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)) / len(actual)


def wape(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    """Σ|error| / Σ|actual|; None when the actuals sum to zero (undefined, not zero error)."""
    if len(actual) != len(predicted) or not actual:
        msg = "WAPE needs equal-length, non-empty series"
        raise ValueError(msg)
    denom = sum(abs(a) for a in actual)
    if denom == 0:
        return None
    return sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)) / denom
