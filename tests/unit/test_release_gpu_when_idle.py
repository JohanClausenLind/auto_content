"""The model servers come down when no run is left to use them.

A server keeps its weights loaded so the next request does not pay the ~72 s load. Right while
work is queued, wrong once the queue is empty: measured 2026-09-10, an idle HiDream held
**18,936 MiB** and `gpu status` reported 3.2 GiB free, so the other GPU tenant on this machine
could not have used the card. Releasing it took free VRAM back to 21.9 GiB.

Not an energy saving, and the tests say so rather than implying it: idle draw was 34.11 W with the
model resident and 34.09 W without, both at P8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.runners import local as runner


@pytest.fixture
def freed(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_free(*, unload_ollama: bool = True) -> dict:
        calls.append({"unload_ollama": unload_ollama})
        return {"stopped": ["hidream"], "ollama_unloaded": []}

    import content_factory.services.local as services

    monkeypatch.setattr(services, "free_the_gpu", fake_free)
    return calls


def test_the_card_is_released_when_nothing_else_is_running(freed, monkeypatch) -> None:
    from content_factory.runners import registry

    monkeypatch.setattr(registry, "active_runs", lambda **_kw: [])
    said: list[str] = []
    out = runner._release_gpu_if_idle(said.append)
    assert freed == [{"unload_ollama": False}]
    assert out is not None and out["stopped"] == ["hidream"]
    # The operator is told, because 19 GB coming back is worth one line.
    assert said and "released the GPU" in said[0] and "hidream" in said[0]


def test_another_running_run_keeps_its_weights(freed, monkeypatch) -> None:
    """The point of a resident server is the run that has not finished yet."""
    from content_factory.runners import registry

    monkeypatch.setattr(registry, "active_runs", lambda **_kw: [object()])
    assert runner._release_gpu_if_idle(lambda _m: None) is None
    assert freed == []


def test_the_setting_can_hold_the_weights_open(freed, monkeypatch) -> None:
    from content_factory.config import settings as settings_mod

    cfg = settings_mod.get_settings()
    # The settings models are frozen, which is the point of them: build a copy instead.
    held = cfg.model_copy(
        update={
            "local_services": cfg.local_services.model_copy(update={"release_when_idle": False})
        }
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: held)
    assert runner._release_gpu_if_idle(lambda _m: None) is None
    assert freed == []


def test_ollama_is_left_alone(freed, monkeypatch) -> None:
    """`free_the_gpu` evicts the text models too by default. Releasing our own weights is ours to
    decide; a neighbour's are not, and nothing here loaded them."""
    from content_factory.runners import registry

    monkeypatch.setattr(registry, "active_runs", lambda **_kw: [])
    runner._release_gpu_if_idle(lambda _m: None)
    assert freed[0]["unload_ollama"] is False


def test_a_failed_run_still_releases_the_card(tmp_path: Path, monkeypatch) -> None:
    """A crash is exactly when a forgotten 19 GB is least likely to be noticed, so the release
    sits in a `finally` rather than on the success path."""
    seen: list[str] = []
    monkeypatch.setattr(runner, "_release_gpu_if_idle", lambda _log=print: seen.append("released"))
    monkeypatch.setattr(runner, "_refuse_while_gpu_claimed", lambda: None)

    def boom(*_a, **_kw):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(runner, "_run_steps", boom)
    ctx = runner.make_context(project_dir=tmp_path / "p")
    with pytest.raises(RuntimeError, match="stage exploded"):
        runner.run_plan([], ctx, workflow="test-lane")
    assert seen == ["released"]
