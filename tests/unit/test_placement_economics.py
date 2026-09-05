from __future__ import annotations

from content_factory.budgets.ledger import Cap, CostLedger, Scope
from content_factory.placement import (
    billed_seconds,
    choose_placement,
    estimate_candidate,
    should_migrate,
)
from content_factory.placement.supply import FixtureOfferSource, fresh_offers
from content_factory.schemas.offers import ComputeOffer, OfferPrice
from content_factory.schemas.placement import PlacementCandidate, PlacementRequest

NOW = "2026-09-05T12:00:00+00:00"


def _req(**kw) -> PlacementRequest:
    return PlacementRequest(task_fingerprint="fp-test", **kw)


def _cand(candidate_id: str, **kw) -> PlacementCandidate:
    base = {
        "candidate_id": candidate_id,
        "kind": "cloud_offer",
        "vram_gb": 24,
        "rate_usd_per_hour": 0.5,
        "expected_useful_execution_s": 3600,
    }
    base.update(kw)
    return PlacementCandidate(**base)


def test_synthetic_regression_expensive_but_fast_candidate_wins() -> None:
    """The prompt's invented A/B example (not a benchmark): B must win."""
    a = _cand(
        "offer-a",
        rate_usd_per_hour=0.25,
        expected_useful_execution_s=3600,
        expected_other_billed_s=600,
        transfer_usd=0.06,
        expected_retry_usd=0.08,
    )
    b = _cand(
        "offer-b",
        rate_usd_per_hour=0.80,
        expected_useful_execution_s=840,
        expected_other_billed_s=240,
        transfer_usd=0.04,
        expected_retry_usd=0.01,
    )
    ea, eb = estimate_candidate(a), estimate_candidate(b)
    assert abs(ea.total_usd - 0.4317) < 0.001
    assert abs(eb.total_usd - 0.29) < 0.0001
    decision = choose_placement(_req(), [a, b], decided_at=NOW)
    assert decision.chosen_candidate_id == "offer-b" and decision.outcome == "dispatch"
    assert any(e.candidate_id == "offer-a" for e in decision.considered)  # considered, not hidden


def test_waiting_locally_wins_when_no_deadline() -> None:
    wait = _cand(
        "wait-local",
        kind="wait_local",
        rate_usd_per_hour=0.03,  # marginal electricity under the configured local policy
        expected_useful_execution_s=3600,
        queue_delay_s=7200,
    )
    cloud = _cand("offer-b", rate_usd_per_hour=0.80, expected_useful_execution_s=840)
    decision = choose_placement(_req(), [wait, cloud], decided_at=NOW)
    assert decision.chosen_candidate_id == "wait-local" and decision.outcome == "wait"

    # With a deadline the wait's p90 completion disqualifies it and the cloud dispatches.
    urgent = choose_placement(_req(deadline_s=3600), [wait, cloud], decided_at=NOW)
    assert urgent.chosen_candidate_id == "offer-b" and urgent.outcome == "dispatch"
    assert any("deadline" in r.reason for r in urgent.rejected)


def test_cold_model_download_changes_the_winner() -> None:
    local = _cand(
        "local-a",
        kind="local_node",
        rate_usd_per_hour=0.30,
        expected_useful_execution_s=3600,
        cache_state="warm",
    )
    cloud_cold = _cand(
        "offer-c",
        rate_usd_per_hour=0.50,
        expected_useful_execution_s=1800,
        expected_other_billed_s=1200,  # image pull + model download, billed
        transfer_usd=0.10,
        cache_state="cold",
    )
    cloud_warm = cloud_cold.model_copy(
        update={"expected_other_billed_s": 60, "transfer_usd": 0.0, "cache_state": "warm"}
    )
    cold = choose_placement(_req(), [local, cloud_cold], decided_at=NOW)
    assert cold.chosen_candidate_id == "local-a"
    warm = choose_placement(_req(), [local, cloud_warm], decided_at=NOW)
    assert warm.chosen_candidate_id == "offer-c"


def test_minimum_rental_changes_the_winner() -> None:
    fast_no_min = _cand(
        "offer-x", rate_usd_per_hour=2.0, expected_useful_execution_s=300, min_billed_s=0
    )
    steady = _cand("offer-y", rate_usd_per_hour=1.0, expected_useful_execution_s=900)
    assert (
        choose_placement(_req(), [fast_no_min, steady], decided_at=NOW).chosen_candidate_id
        == "offer-x"
    )
    fast_with_min = fast_no_min.model_copy(update={"min_billed_s": 3600})
    assert estimate_candidate(fast_with_min).billed_s == 3600  # minimum charged duration applies
    assert (
        choose_placement(_req(), [fast_with_min, steady], decided_at=NOW).chosen_candidate_id
        == "offer-y"
    )


def test_reliability_requirement_excludes_spot() -> None:
    spot = _cand("offer-spot", spot=True, rate_usd_per_hour=0.2)
    on_demand = _cand("offer-od", rate_usd_per_hour=0.6)
    decision = choose_placement(_req(require_reliable=True), [spot, on_demand], decided_at=NOW)
    assert decision.chosen_candidate_id == "offer-od"
    assert any("interruptible" in r.reason for r in decision.rejected)


def test_vram_makes_only_the_larger_gpu_feasible() -> None:
    small = _cand("offer-24g", vram_gb=24, rate_usd_per_hour=0.3)
    large = _cand("offer-48g", vram_gb=48, rate_usd_per_hour=1.2)
    decision = choose_placement(_req(vram_gb_required=40), [small, large], decided_at=NOW)
    assert decision.chosen_candidate_id == "offer-48g"
    assert any("VRAM" in r.reason for r in decision.rejected)


def test_budget_reservation_is_atomic_and_pauses_on_exhaustion() -> None:
    ledger = CostLedger()
    ledger.set_cap(Cap(scope=Scope.project, key="prj1", limit_usd=0.30))
    cand = _cand("offer-b", rate_usd_per_hour=0.80, expected_useful_execution_s=1260)  # $0.28
    req = _req(budget_scope_keys=(("project", "prj1"),))
    first = choose_placement(req, [cand], decided_at=NOW, ledger=ledger)
    assert first.outcome == "dispatch" and first.budget_reservation_id is not None
    # The same remaining funds cannot be reserved twice: the second placement pauses.
    second = choose_placement(req, [cand], decided_at=NOW, ledger=ledger)
    assert second.outcome == "pause_budget" and second.chosen_candidate_id is None


def test_no_feasible_candidate_is_a_typed_pause_not_a_downgrade() -> None:
    small = _cand("offer-24g", vram_gb=24)
    decision = choose_placement(_req(vram_gb_required=80), [small], decided_at=NOW)
    assert decision.outcome == "pause_no_candidate" and decision.chosen_candidate_id is None


def test_running_tasks_do_not_chase_slightly_cheaper_offers() -> None:
    assert not should_migrate(expected_remaining_usd=0.50, new_total_usd=0.30, switch_cost_usd=0.25)
    assert should_migrate(expected_remaining_usd=0.50, new_total_usd=0.30, switch_cost_usd=0.15)


def test_billing_rounding_applies_once_to_the_allocation() -> None:
    # 61 s of work on a per-minute-billed offer = 2 billed minutes, not 2 per GPU or per phase.
    assert billed_seconds(31, 30, min_billed_s=0, increment_s=60) == 120
    assert billed_seconds(10, 0, min_billed_s=300, increment_s=60) == 300


def test_decisions_are_deterministic_and_self_describing() -> None:
    cands = [
        _cand("offer-a", rate_usd_per_hour=0.25, expected_other_billed_s=600),
        _cand("offer-b", rate_usd_per_hour=0.80, expected_useful_execution_s=840),
    ]
    d1 = choose_placement(_req(), cands, decided_at=NOW)
    d2 = choose_placement(_req(), cands, decided_at=NOW)
    assert d1.content_hash() == d2.content_hash()
    assert d1.explanation and d1.policy_version


def test_offer_contract_and_freshness() -> None:
    def offer(offer_id: str, expires: str | None) -> ComputeOffer:
        return ComputeOffer(
            offer_id=offer_id,
            source_api="fixture",
            provider="fixture-cloud",
            retrieved_at=NOW,
            expires_at=expires,
            gpu_model="RTX 4090",
            gpu_count=1,
            vram_gb_per_gpu=24,
            price=OfferPrice(currency="USD", usd_per_hour=0.5, original_amount_per_hour=0.5),
        )

    src = FixtureOfferSource(
        offers=(offer("live", "2026-09-05T13:00:00+00:00"), offer("stale", NOW))
    )
    fresh = fresh_offers(src.list_offers(), now_iso=NOW)
    assert [o.offer_id for o in fresh] == ["live"]

    import pytest

    with pytest.raises(ValueError, match="VRAM"):
        ComputeOffer(
            offer_id="bad",
            source_api="fixture",
            provider="fixture-cloud",
            retrieved_at=NOW,
            gpu_model="RTX 4090",
            gpu_count=1,
            vram_gb_per_gpu=0,
            price=OfferPrice(currency="USD", usd_per_hour=0.5, original_amount_per_hour=0.5),
        )
