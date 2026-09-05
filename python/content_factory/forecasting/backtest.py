"""Rolling-origin backtesting with no future-data leakage, by construction.

For each origin, the model function receives ONLY observations strictly before the origin and
must return ``horizon`` predictions, which are scored against the actuals it never saw. Results
carry sample counts so a lucky single window cannot masquerade as skill.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from content_factory.forecasting.baselines import mae, wape

ModelFn = Callable[[Sequence[float], int], Sequence[float]]  # (history, horizon) -> predictions


@dataclass(frozen=True)
class BacktestWindow:
    origin: int
    mae: float
    wape: float | None


@dataclass(frozen=True)
class BacktestResult:
    windows: tuple[BacktestWindow, ...]
    mean_mae: float
    mean_wape: float | None  # None when every window's actuals summed to zero
    n_windows: int


def rolling_origin_backtest(
    series: Sequence[float],
    model_fn: ModelFn,
    *,
    horizon: int,
    min_train: int,
    step: int = 1,
) -> BacktestResult:
    if min_train < 1 or horizon < 1:
        msg = "min_train and horizon must be positive"
        raise ValueError(msg)
    if len(series) < min_train + horizon:
        msg = f"series of {len(series)} cannot back-test min_train={min_train}, horizon={horizon}"
        raise ValueError(msg)
    windows: list[BacktestWindow] = []
    for origin in range(min_train, len(series) - horizon + 1, step):
        train = list(series[:origin])  # a copy: the model can never touch the future
        actual = list(series[origin : origin + horizon])
        predicted = list(model_fn(train, horizon))
        if len(predicted) != horizon:
            msg = f"model returned {len(predicted)} values for horizon {horizon}"
            raise ValueError(msg)
        windows.append(
            BacktestWindow(origin=origin, mae=mae(actual, predicted), wape=wape(actual, predicted))
        )
    wapes = [w.wape for w in windows if w.wape is not None]
    return BacktestResult(
        windows=tuple(windows),
        mean_mae=sum(w.mae for w in windows) / len(windows),
        mean_wape=(sum(wapes) / len(wapes)) if wapes else None,
        n_windows=len(windows),
    )
