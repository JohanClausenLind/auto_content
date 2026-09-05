from __future__ import annotations

import pytest

from content_factory.forecasting import (
    available_adapters,
    rolling_mean_forecast,
    rolling_origin_backtest,
    seasonal_naive_forecast,
    wape,
)


def test_only_offline_baselines_are_available_by_default() -> None:
    assert available_adapters() == ("seasonal_naive", "rolling_mean")


def test_seasonal_naive_repeats_the_last_season() -> None:
    history = [float(x) for x in range(1, 15)]  # …, 8..14 is the last week
    assert seasonal_naive_forecast(history, season=7, horizon=7) == [8, 9, 10, 11, 12, 13, 14]
    assert seasonal_naive_forecast(history, season=7, horizon=9)[:2] == [8, 9]
    with pytest.raises(ValueError, match="full season"):
        seasonal_naive_forecast([1.0, 2.0], season=7, horizon=1)


def test_wape_handles_zero_containing_series_where_mape_cannot() -> None:
    assert wape([0.0, 0.0, 2.0], [0.0, 1.0, 2.0]) == pytest.approx(0.5)
    assert wape([0.0, 0.0], [1.0, 1.0]) is None  # undefined, not silently zero


def test_backtest_never_shows_the_model_the_future() -> None:
    series = [float(x) for x in range(40)]
    seen_lengths: list[int] = []

    def last_value_model(history, horizon):
        seen_lengths.append(len(history))
        return [history[-1]] * horizon

    result = rolling_origin_backtest(series, last_value_model, horizon=5, min_train=20, step=5)
    # Each window trained on exactly the observations before its origin — nothing more.
    assert seen_lengths == [20, 25, 30, 35]
    assert result.n_windows == 4
    assert result.mean_mae == pytest.approx(3.0)  # last-value on a ramp: errors 1..5 → mean 3


def test_seasonal_baseline_beats_rolling_mean_on_a_seasonal_series() -> None:
    series = [10.0, 1.0, 1.0] * 20
    seasonal = rolling_origin_backtest(
        series,
        lambda h, n: seasonal_naive_forecast(h, season=3, horizon=n),
        horizon=3,
        min_train=12,
    )
    rolling = rolling_origin_backtest(
        series,
        lambda h, n: rolling_mean_forecast(h, window=3, horizon=n),
        horizon=3,
        min_train=12,
    )
    assert seasonal.mean_mae < rolling.mean_mae  # a candidate model must beat THIS bar


def test_backtest_rejects_a_model_returning_the_wrong_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        rolling_origin_backtest([1.0] * 30, lambda h, n: [1.0] * (n - 1), horizon=3, min_train=10)
