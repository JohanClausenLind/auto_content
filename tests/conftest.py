"""Suite-wide hygiene: tests must not inherit this host's configuration, or write into it."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    monkeypatch.delenv("CF_CONFIG_FILE", raising=False)
    for name in [key for key in os.environ if key.startswith("CF__")]:
        monkeypatch.delenv(name)
    services: Path = tmp_path_factory.mktemp("services")
    monkeypatch.setenv("CF_SERVICES_DIR", str(services))
    # And the third direction: the core suite must not reach a GPU, and a *lane definition* is now
    # allowed to say which real model it uses.
    monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
    monkeypatch.setenv("CF__VIDEO__BACKEND", "mock")
