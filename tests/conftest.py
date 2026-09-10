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
    # And the third direction: the core suite must not reach a GPU, and a *lane definition* is
    # now allowed to say which real model it uses (`generate_anchor.model`,
    # `generate_keyframes.model`, `generate_video.model`) — which is right for a film and wrong
    # for an offline test. Deleting `CF__*` above leaves the settings at their defaults, and a
    # default is exactly what a lane's widget is allowed to override, so `hybrid-video`'s
    # end-to-end test started a HiDream server (measured 2026-09-10). Configuring the mocks here
    # takes the higher precedence for the whole suite; a test that wants something else still
    # wins, because `monkeypatch.setenv` in the test runs after this fixture.
    monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
    monkeypatch.setenv("CF__VIDEO__BACKEND", "mock")
