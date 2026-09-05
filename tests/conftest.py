"""Suite-wide hygiene: tests must not inherit this host's configuration, or write into it.

``CF__*`` environment overrides and a host-set ``CF_CONFIG_FILE`` would leak the
developer's real settings (e.g. extra model roots from .env) into every
``Settings()`` a test constructs. Tests that need overrides set them explicitly
with ``monkeypatch.setenv``, which runs after this fixture.

``CF_SERVICES_DIR`` is the other direction: a test that runs stages registers itself as an
active run, and the registry must not land in the checkout's ``.services/`` beside the pids of
the real HiDream server — a ``content-factory stop`` in another terminal would then find a run
belonging to a test process that has long since exited.
"""

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
