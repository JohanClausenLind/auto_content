"""Startup invariants that protect exposure (3.3, 25): funnel needs password+MFA; no 0.0.0.0."""

from __future__ import annotations

import pytest

from content_factory.config.settings import Settings


def _settings(**over: object) -> Settings:
    return Settings.model_validate({"database": {"url_env": "DATABASE_URL"}, **over})


def test_defaults_are_inert() -> None:
    s = _settings()
    assert s.bind == "127.0.0.1"
    assert s.distribution.enabled is False and s.distribution.kill_switch is True
    assert s.engagement.enabled is False
    assert s.personas.firewall_required is True
    assert s.tailscale.mode == "off"


def test_funnel_refuses_without_mfa() -> None:
    with pytest.raises(ValueError, match="funnel requires MFA"):
        _settings(
            tailscale={"mode": "funnel"}, auth={"totp_enabled": False, "passkeys_enabled": False}
        )
    with pytest.raises(ValueError, match="auth.mode=local"):
        _settings(tailscale={"mode": "funnel"}, auth={"mode": "oidc"})
    assert _settings(tailscale={"mode": "funnel"}).tailscale.mode == "funnel"


def test_binding_all_interfaces_is_refused() -> None:
    with pytest.raises(ValueError, match="0.0.0.0"):
        _settings(bind="0.0.0.0")


def test_unknown_config_keys_are_errors() -> None:
    with pytest.raises(ValueError):
        _settings(billing={"plan": "pro"})
    with pytest.raises(ValueError):
        _settings(personas={"firewall_required": False})


def test_env_override_wins_over_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF__BUDGETS__MONTHLY_EXTERNAL_USD", "7.5")
    monkeypatch.setenv("CF__DISTRIBUTION__KILL_SWITCH", "true")
    s = Settings()
    assert s.budgets.monthly_external_usd == 7.5
    assert s.distribution.kill_switch is True
