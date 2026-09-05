from __future__ import annotations

import pytest

from content_factory.energy import attribute_energy, energy_cost, integrate_counter, integrate_power
from content_factory.schemas.energy import EnergyReport, EnergyShare, PowerSample, Tariff


def _sample(hour: int, watts: float, cumulative_wh: float | None = None) -> PowerSample:
    return PowerSample(
        at=f"2026-09-05T{hour:02d}:00:00+00:00",
        watts=watts,
        cumulative_wh=cumulative_wh,
        source="wall_meter",
    )


def test_constant_load_integrates_to_the_arithmetic_answer() -> None:
    samples = [_sample(h, 500.0) for h in range(9)]  # 500 W held for 8 hours
    kwh, gaps = integrate_power(samples, max_gap_s=3700)
    assert kwh == pytest.approx(4.0) and gaps == ()


def test_sampling_holes_become_gaps_not_invented_energy() -> None:
    samples = [_sample(0, 500), _sample(1, 500), _sample(5, 500), _sample(6, 500)]
    kwh, gaps = integrate_power(samples, max_gap_s=3700)
    assert kwh == pytest.approx(1.0)  # only the two measured hours count
    assert gaps == (("2026-09-05T01:00:00+00:00", "2026-09-05T05:00:00+00:00"),)


def test_cumulative_counter_handles_a_meter_reset() -> None:
    samples = [
        _sample(0, 0, cumulative_wh=100),
        _sample(1, 0, cumulative_wh=350),  # +250
        _sample(2, 0, cumulative_wh=50),  # reset: count the 50 accumulated since restart
        _sample(3, 0, cumulative_wh=150),  # +100
    ]
    assert integrate_counter(samples) == pytest.approx(0.4)


def test_attribution_reconciles_jobs_and_idle_to_the_measured_total() -> None:
    shares = attribute_energy(
        4.0,
        "2026-09-05T00:00:00+00:00",
        "2026-09-05T04:00:00+00:00",
        [
            ("job-1", "2026-09-05T00:00:00+00:00", "2026-09-05T02:00:00+00:00"),
            ("job-2", "2026-09-05T01:00:00+00:00", "2026-09-05T03:00:00+00:00"),
        ],
    )
    by_key = {s.key: s.kwh for s in shares}
    assert by_key["job-1"] == pytest.approx(1.5)  # 1.0 alone + 0.5 shared
    assert by_key["job-2"] == pytest.approx(1.5)
    assert by_key["idle"] == pytest.approx(1.0)
    assert sum(by_key.values()) == pytest.approx(4.0)


def test_energy_report_refuses_attribution_that_does_not_reconcile() -> None:
    with pytest.raises(ValueError, match="reconcile"):
        EnergyReport(
            node_id="nde_test0000001",
            source="wall_meter",
            window_start="2026-09-05T00:00:00+00:00",
            window_end="2026-09-05T04:00:00+00:00",
            kwh_measured=4.0,
            attribution=(EnergyShare(key="job-1", kwh=1.0),),  # 3 kWh vanished
        )
    with pytest.raises(ValueError, match="come together"):
        EnergyReport(
            node_id="nde_test0000001",
            source="wall_meter",
            window_start="2026-09-05T00:00:00+00:00",
            window_end="2026-09-05T04:00:00+00:00",
            kwh_measured=4.0,
            cost=10.0,  # cost without a currency
        )


def test_tariff_cost_and_component_bounds() -> None:
    tariff = Tariff(
        tariff_id="se-static-2026",
        currency="SEK",
        price_per_kwh=2.5,
        components={"energy": 1.2, "network": 0.8, "tax": 0.5},
        valid_from="2026-01-01",
    )
    # The prompt's synthetic example: 240 kWh of active use at the configured all-in tariff.
    assert energy_cost(240.0, tariff) == pytest.approx(600.0)
    with pytest.raises(ValueError, match="exceed"):
        Tariff(
            tariff_id="bad",
            currency="SEK",
            price_per_kwh=1.0,
            components={"energy": 2.0},
            valid_from="2026-01-01",
        )
