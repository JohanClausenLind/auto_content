from __future__ import annotations

import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from content_factory.budgets.ledger import BudgetExceededError, Cap, CostLedger, Scope


def ledger(limit: float = 10.0) -> CostLedger:
    led = CostLedger()
    led.set_cap(Cap(scope=Scope.monthly, key="2026-09", limit_usd=limit))
    led.set_cap(Cap(scope=Scope.project, key="prj_x", limit_usd=limit / 2))
    return led


def test_reserve_settle_and_hard_stop() -> None:
    led = ledger()
    scopes = [(Scope.monthly, "2026-09"), (Scope.project, "prj_x")]
    r1 = led.reserve(3.0, scopes=scopes, purpose="tts")
    assert led.headroom(Scope.project, "prj_x") == 2.0
    led.settle(r1, 2.5, provider="elevenlabs")
    assert led.spent(Scope.project, "prj_x") == 2.5
    led.reserve(2.5, scopes=scopes, purpose="images")
    with pytest.raises(BudgetExceededError):
        led.reserve(0.5, scopes=scopes, purpose="over the project cap")
    with pytest.raises(Exception, match="unknown or already settled"):
        led.settle(r1, 1.0, provider="x")


def test_release_returns_headroom_and_warnings_fire() -> None:
    led = ledger()
    r = led.reserve(4.0, scopes=[(Scope.project, "prj_x")], purpose="render")
    assert led.headroom(Scope.project, "prj_x") == 1.0
    assert led.warnings and "prj_x" in led.warnings[0]
    led.release(r)
    assert led.headroom(Scope.project, "prj_x") == 5.0
    led.release(r)  # idempotent


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.tuples(st.floats(min_value=0, max_value=3), st.floats(min_value=0, max_value=1)),
        min_size=1,
        max_size=25,
    )
)
def test_committed_never_exceeds_cap(ops: list[tuple[float, float]]) -> None:
    led = CostLedger()
    led.set_cap(Cap(scope=Scope.monthly, key="m", limit_usd=10.0))
    for estimate, settle_ratio in ops:
        try:
            r = led.reserve(estimate, scopes=[(Scope.monthly, "m")], purpose="p")
        except BudgetExceededError:
            continue
        led.settle(r, estimate * settle_ratio, provider="p")
        assert led.spent(Scope.monthly, "m") <= 10.0 + 1e-6
    assert led.headroom(Scope.monthly, "m") >= -1e-6


def test_concurrent_reservations_respect_the_cap() -> None:
    led = CostLedger()
    led.set_cap(Cap(scope=Scope.monthly, key="m", limit_usd=10.0))
    granted: list[float] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            led.reserve(1.0, scopes=[(Scope.monthly, "m")], purpose="p")
            granted.append(1.0)
        except BudgetExceededError:
            pass
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(granted) == 10
