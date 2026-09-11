"""Local GPU services: stages start HiDream / ComfyUI themselves, one GPU tenant at a time, and
link the packages' weights into ComfyUI before it starts. All process and HTTP seams are faked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from content_factory.config.settings import LocalServicesSettings
from content_factory.schemas.comfyui import RequiredModel
from content_factory.services import local as svc
from content_factory.services.local import (
    LocalServices,
    ServiceError,
    link_required_models,
)


class FakeWorld:
    """Which tenants answer HTTP, what processes were spawned/run/killed."""

    def __init__(self) -> None:
        self.up: dict[str, str] = {}  # tenant -> "loading" | "ready"
        self.popen: list[list[str]] = []
        self.run: list[list[str]] = []
        self.killed: list[int] = []
        self.pids: dict[str, int] = {}
        self.clock = 0.0
        self.ollama = True
        self.ollama_loaded: list[str] = ["qwen38-ridge:latest", "qwen3:8b"]
        """What Ollama says is resident. "Free the GPU" has to be a no-op when it is already
        free, or every stage that calls it reaches into a service nobody asked it to touch."""
        self.requests: list[httpx.Request] = []
        self.env: list[dict[str, str]] = []
        """The environment each spawn was given. A tenant's *weights* are decided here and
        nowhere else — CF_HIDREAM_MODEL_TYPE, and now CF_HIDREAM_LORA — so a wiring bug in this
        dict is a server quietly serving a different model than the run believes it asked for."""

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            port = request.url.port
            if port == 11434:
                if not self.ollama:
                    raise httpx.ConnectError("refused")
                if request.url.path == "/api/ps":
                    return httpx.Response(
                        200, json={"models": [{"model": m} for m in self.ollama_loaded]}
                    )
                return httpx.Response(200, json={"done": True})
            tenant = "hidream" if port == 8801 else "comfyui"
            state = self.up.get(tenant)
            if state is None:
                raise httpx.ConnectError("refused")
            if tenant == "hidream":
                return httpx.Response(200, json={"loaded": state == "ready"})
            return httpx.Response(200, json={"system": {}})

        return httpx.MockTransport(handler)

    def fake_popen(self, cmd, **kw):
        self.popen.append(list(cmd))
        self.env.append(dict(kw.get("env") or {}))
        tenant = "comfyui" if any(str(c).endswith("main.py") for c in cmd) else "hidream"
        self.up[tenant] = "ready" if tenant == "comfyui" else "loading"
        self.pids[tenant] = 4242 if tenant == "hidream" else 5150

        class P:
            pid = self.pids[tenant]

        return P()

    def fake_run(self, cmd, **kw):
        self.run.append(list(cmd))
        if cmd[2:3] == ["stop"]:
            self.up.pop("comfyui", None)
        if cmd[2:3] == ["which"]:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"data": {"workspace_path": "/tmp/comfy-ws"}}), ""
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def sleep(self, s: float) -> None:
        self.clock += s
        # a loading HiDream becomes ready after a few polls; a killed one goes away
        if self.up.get("hidream") == "loading":
            self.up["hidream"] = "ready"

    def monotonic(self) -> float:
        return self.clock


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeWorld:
    w = FakeWorld()
    monkeypatch.setattr(svc, "SUBPROCESS_POPEN", w.fake_popen)
    monkeypatch.setattr(svc, "SUBPROCESS_RUN", w.fake_run)
    # Off unless a test asks for it: whether this machine has a *user* systemd instance is a fact
    # about the machine, and letting the probe run would make every assertion about the spawned
    # command depend on where the suite happens to be executing.
    monkeypatch.setattr(svc, "SYSTEMD_RUN_AVAILABLE", lambda: False)

    real_kill = svc.os.killpg

    def fake_killpg(pid: int, sig: int) -> None:
        w.killed.append(pid)
        for tenant, tenant_pid in list(w.pids.items()):
            if tenant_pid == pid:
                w.up.pop(tenant, None)

    monkeypatch.setattr(svc.os, "killpg", fake_killpg)
    assert real_kill is not fake_killpg
    return w


def _services(world: FakeWorld, tmp_path: Path, **overrides) -> LocalServices:
    cfg = LocalServicesSettings(
        comfy_workspace=str(tmp_path / "comfy"),
        comfy_models_dir=str(tmp_path / "comfy" / "models"),
        weight_store=str(tmp_path / "store"),
        poll_interval_s=1.0,
        **overrides,
    )
    return LocalServices(
        cfg,
        hidream_endpoint="http://127.0.0.1:8801",
        comfy_endpoint="http://127.0.0.1:8188",
        state_dir=tmp_path / ".services",
        transport=world.transport(),
        sleep=world.sleep,
        monotonic=world.monotonic,
        repo_root=tmp_path,
    )


def test_ensure_starts_hidream_and_waits_for_the_model_to_load(world: FakeWorld, tmp_path: Path):
    s = _services(world, tmp_path)
    assert s.ensure("hidream") == "http://127.0.0.1:8801"
    assert world.popen and world.popen[0][:3] == ["uv", "run", "--project"]
    assert world.popen[0][-1].endswith("skills/image/hidream/server.py")
    assert json.loads((tmp_path / ".services" / "hidream.json").read_text())["pid"] == 4242
    # already healthy: nothing else is started
    s.ensure("hidream")
    assert len(world.popen) == 1


def test_no_lora_is_configured_by_default(world: FakeWorld, tmp_path: Path):
    """Merging an adapter changes every picture the server makes; nobody gets that by upgrading."""
    _services(world, tmp_path).ensure("hidream")
    assert "CF_HIDREAM_LORA" not in world.env[0]


def test_a_configured_lora_reaches_the_server_at_spawn(world: FakeWorld, tmp_path: Path):
    """An adapter is part of what the server IS, so it is set once at spawn and never per request.

    Every frame of a sequence has to come off the same weights or the GenerationLock freezes a
    model that changed underneath it — and with a pool of hosts, "same endpoint" is not "same
    model", which is why /healthz reports the adapter back.
    """
    adapter = tmp_path / "romsketch_ho1_v1b.safetensors"
    adapter.write_bytes(b"")
    services = _services(world, tmp_path, hidream_lora=str(adapter), hidream_lora_multiplier=0.8)
    services.ensure("hidream")
    assert world.env[0]["CF_HIDREAM_LORA"] == str(adapter)
    assert world.env[0]["CF_HIDREAM_LORA_MULTIPLIER"] == "0.8"


def test_a_relative_lora_path_resolves_against_the_repo_not_the_cwd(
    world: FakeWorld, tmp_path: Path
):
    """`datasets/training/romsketch/...` is how an operator would write it, and the server is
    spawned detached with its own cwd."""
    rel = "datasets/training/romsketch/output/romsketch_ho1_v1b.safetensors"
    _services(world, tmp_path, hidream_lora=rel).ensure("hidream")
    assert world.env[0]["CF_HIDREAM_LORA"] == str(tmp_path / rel)


def test_comfyui_start_stops_hidream_first_and_links_models(world: FakeWorld, tmp_path: Path):
    store = tmp_path / "store"
    (store / "ltx25" / "diffusion_models").mkdir(parents=True)
    (
        store / "ltx25" / "diffusion_models" / "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf"
    ).write_bytes(b"x")
    s = _services(world, tmp_path)
    s.ensure("hidream")
    s.ensure("comfyui")
    assert world.killed == [4242]  # HiDream released the GPU before ComfyUI loaded
    launch = next(c for c in world.popen if str(c[1]).endswith("main.py"))
    assert launch[1] == str(tmp_path / "comfy" / "main.py")  # run from the workspace, its venv
    assert launch[2:] == [
        "--cache-none",
        "--reserve-vram",
        "1.5",
        "--listen",
        "127.0.0.1",
        "--port",
        "8188",
    ]
    links = json.loads((tmp_path / ".services" / "comfy-links.json").read_text())
    assert "diffusion_models/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf" in links["linked"]
    assert "unet/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf" in links["linked"]  # GGUF alias
    assert any(m.startswith("vae/") for m in links["missing"])  # store lacks the VAE here
    assert not (tmp_path / ".services" / "hidream.json").exists()


def test_hidream_start_stops_comfyui_first(world: FakeWorld, tmp_path: Path):
    s = _services(world, tmp_path)
    s.ensure("comfyui")
    s.ensure("hidream")
    assert 5150 in world.killed and "comfyui" not in world.up


def test_operator_started_comfyui_is_stopped_through_comfy_cli(world: FakeWorld, tmp_path: Path):
    world.up["comfyui"] = "ready"  # running, but no pid recorded by us and pgrep finds nothing
    s = _services(world, tmp_path)
    s.ensure("hidream")
    assert ["comfy", "--skip-prompt", "stop"] in world.run and "comfyui" not in world.up


def test_auto_start_off_explains_the_manual_command(world: FakeWorld, tmp_path: Path):
    s = _services(world, tmp_path, auto_start=False)
    with pytest.raises(ServiceError, match=r"main\.py --cache-none --reserve-vram 1\.5"):
        s.ensure("comfyui")
    with pytest.raises(ServiceError, match=r"skills/image/hidream/server\.py"):
        s.ensure("hidream")
    assert not world.popen


def test_startup_timeout_is_reported_with_the_log_path(
    world: FakeWorld, tmp_path: Path, monkeypatch
):
    def never_ready(s: float) -> None:
        world.clock += s

    s = _services(world, tmp_path, startup_timeout_s=30)
    s._sleep = never_ready  # type: ignore[assignment]
    with pytest.raises(ServiceError, match=r"hidream\.log"):
        s.ensure("hidream")


def test_link_required_models_is_idempotent_and_never_replaces(tmp_path: Path):
    store = tmp_path / "store"
    (store / "wan-animate-2").mkdir(parents=True)
    (store / "wan-animate-2" / "wan_animate_2-Q5_K_M.gguf").write_bytes(b"g")
    (store / "wan21-repack" / "split_files" / "vae").mkdir(parents=True)
    (store / "wan21-repack" / "split_files" / "vae" / "wan_2.1_vae.safetensors").write_bytes(b"v")
    comfy = tmp_path / "comfy" / "models"
    (comfy / "vae").mkdir(parents=True)
    (comfy / "vae" / "wan_2.1_vae.safetensors").write_bytes(b"already here")
    models = [
        RequiredModel(
            filename="wan_animate_2-Q5_K_M.gguf",
            relative_path="models/diffusion_models",
            source_url="x",
            license="l",
        ),
        RequiredModel(
            filename="wan_2.1_vae.safetensors",
            relative_path="models/vae",
            source_url="x",
            license="l",
        ),
        RequiredModel(
            filename="clip_vision_h.safetensors",
            relative_path="models/clip_vision",
            source_url="x",
            license="l",
        ),
    ]
    first = link_required_models(models, comfy_models_dir=comfy, weight_store=store)
    assert set(first.linked) == {
        "diffusion_models/wan_animate_2-Q5_K_M.gguf",
        "unet/wan_animate_2-Q5_K_M.gguf",
    }
    assert first.present == ("vae/wan_2.1_vae.safetensors",)
    assert first.missing == ("clip_vision/clip_vision_h.safetensors",)
    assert (comfy / "vae" / "wan_2.1_vae.safetensors").read_bytes() == b"already here"
    assert (comfy / "unet" / "wan_animate_2-Q5_K_M.gguf").resolve() == (
        store / "wan-animate-2" / "wan_animate_2-Q5_K_M.gguf"
    )
    second = link_required_models(models, comfy_models_dir=comfy, weight_store=store)
    assert second.linked == () and len(second.present) == 3


def test_backends_map_to_their_tenant_and_mocks_to_none() -> None:
    from content_factory.media.video_generate import ComfyUIVideoBackend, MockVideoBackend
    from content_factory.schemas.fixtures import sample_ltx_i2v_package
    from content_factory.sequences.engine import MockReferenceEditBackend
    from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend
    from content_factory.workflows.stages import _ensure_backend_ready, _service_for_backend

    assert _service_for_backend(MockVideoBackend()) is None
    assert _service_for_backend(MockReferenceEditBackend()) is None
    assert _service_for_backend(HiDreamReferenceEditBackend()) == "hidream"
    comfy = ComfyUIVideoBackend("http://127.0.0.1:8188", sample_ltx_i2v_package())
    assert _service_for_backend(comfy) == "comfyui"
    warmed: set[str] = set()
    _ensure_backend_ready(MockVideoBackend(), warmed)  # never touches services for a mock
    assert warmed == set()


def test_a_pool_entry_on_the_other_box_is_not_this_machines_to_start() -> None:
    """The second GPU host is in `hidream_endpoints`, and starting the local server for it puts
    17-19 GB on a card the run never touches — measured 2026-09-10, and it was what killed the
    `silent-video` running beside it (MMAudio met 15.5 GiB of somebody else's weights)."""
    from content_factory.media.video_generate import ComfyUIVideoBackend
    from content_factory.schemas.fixtures import sample_ltx_i2v_package
    from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend
    from content_factory.workflows.stages import _same_endpoint, _service_for_backend

    remote = HiDreamReferenceEditBackend(endpoint="http://100.82.150.94:8801")
    assert _service_for_backend(remote) is None
    # The same server written three ways is still the managed one.
    for spelling in ("http://127.0.0.1:8801", "http://127.0.0.1:8801/", "127.0.0.1:8801"):
        assert _service_for_backend(HiDreamReferenceEditBackend(endpoint=spelling)) == "hidream"
    assert (
        _service_for_backend(
            ComfyUIVideoBackend("http://100.82.150.94:8188", sample_ltx_i2v_package())
        )
        is None
    )
    assert _same_endpoint("http://host:80", "http://host") and not _same_endpoint("a:1", "a:2")


# --- the third GPU tenant, and what a generation cost ------------------------------------------


def test_ensure_unloads_every_catalogued_ollama_model_before_loading(
    world: FakeWorld, tmp_path: Path
) -> None:
    """Ollama is the third tenant on the card and the only one nothing here managed.

    It keeps a model resident for five minutes after the last request, so a lane that drafts a hook
    with qwen38-ridge (12.6 GB) and then generates an anchor arrives at HiDream's ~19.4 GB load
    with the text model still holding its weights. That does not fail cleanly — the caching
    allocator fragments and dies on a 238 MB allocation, which is the whole reason
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments is set at 30 % of the throughput.
    """
    from content_factory.models.catalog import default_catalog

    s = _services(world, tmp_path)
    assert s.ensure("hidream") == "http://127.0.0.1:8801"

    unloads = [r for r in world.requests if r.url.port == 11434 and r.url.path == "/api/generate"]
    catalogued = {d.model_id for d in default_catalog() if d.provider == "ollama"}
    # Every catalogued model Ollama says is RESIDENT, and nothing else. The quality tier is in the
    # catalogue and not loaded here, so asking it to unload would be a call about nothing.
    expected = sorted(catalogued & set(world.ollama_loaded))
    assert [json.loads(r.content)["model"] for r in unloads] == expected
    assert len(expected) == 2 and len(catalogued) == 3
    assert all(json.loads(r.content)["keep_alive"] == 0 for r in unloads)
    assert all(r.method == "POST" for r in unloads)

    # A tenant that is already healthy returns before any of this: a fully cached rerun must not
    # evict a model somebody else is using.
    world.requests.clear()
    s.ensure("hidream")
    assert [r for r in world.requests if r.url.port == 11434] == []


def test_nothing_resident_means_nothing_is_asked_to_unload(
    world: FakeWorld, tmp_path: Path
) -> None:
    """The guard that keeps this out of an offline test run's way, and out of an operator's.

    Every stage on the model-loading path calls this, so "free the GPU" reaching into Ollama when
    the card is already clear would be a side effect nobody asked for — including during
    ``just test``, which must not disturb the host.
    """
    world.ollama_loaded = []
    s = _services(world, tmp_path)
    assert s.unload_ollama() == ()
    posts = [r for r in world.requests if r.url.path == "/api/generate"]
    assert posts == []

    # One resident model: exactly that one is released, not the whole catalogue.
    world.requests.clear()
    world.ollama_loaded = ["qwen3:8b"]
    assert s.unload_ollama() == ("qwen3:8b",)
    posts = [r for r in world.requests if r.url.path == "/api/generate"]
    assert [json.loads(r.content)["model"] for r in posts] == ["qwen3:8b"]


def test_no_ollama_on_this_machine_is_not_an_error(world: FakeWorld, tmp_path: Path) -> None:
    """The common case on a box that only renders. A refused connection means nothing to release."""
    world.ollama = False
    s = _services(world, tmp_path)
    assert s.unload_ollama() == ()
    assert s.ensure("hidream") == "http://127.0.0.1:8801"  # and the start still happens


def test_free_the_gpu_evicts_both_tenants_and_the_text_models(
    world: FakeWorld, tmp_path: Path, monkeypatch
) -> None:
    """For the stages that load a model in their own uv environment through a subprocess — the post
    chain, sound_design, restore_speech. Nothing stopped HiDream or ComfyUI for those, so the
    second load met a card with 19 GB already on it."""
    from content_factory.services.local import free_the_gpu

    built = _services(world, tmp_path)
    built.ensure("hidream")
    assert world.up.get("hidream") == "ready"
    # free_the_gpu owns the client it opens and closes it on the way out, so the injected
    # factory has to hand back a fresh instance per call, exactly as the real one does.
    monkeypatch.setattr(svc, "LocalServices", lambda *a, **kw: _services(world, tmp_path))

    freed = free_the_gpu()
    assert freed["stopped"] == ["hidream"]
    assert freed["ollama_unloaded"] == ["qwen38-ridge:latest", "qwen3:8b"]
    assert 4242 in world.killed
    # Idempotent: nothing left to stop, and saying so is not an error.
    assert free_the_gpu()["stopped"] == []


def test_ensure_service_closes_the_http_client_it_opened(
    world: FakeWorld, tmp_path: Path, monkeypatch
) -> None:
    """The stage entry points build a LocalServices per call, and the stages call them once per
    stage. An unclosed httpx client is one leaked connection pool per stage of every run."""
    from content_factory.services.local import ensure_service

    built = _services(world, tmp_path)
    monkeypatch.setattr(svc, "LocalServices", lambda *a, **kw: built)

    assert ensure_service("hidream") == "http://127.0.0.1:8801"
    assert built._http.is_closed


def test_a_pid_we_did_not_start_is_signalled_alone_not_by_process_group(
    world: FakeWorld, tmp_path: Path, monkeypatch
) -> None:
    """`pgrep -f ComfyUI/main.py` matches any checkout on the box, including the operator's own
    `~/git/ComfyUI`, and a pid found that way need not lead its process group. `killpg(pid)`
    would then signal whichever group happens to share that number — the operator's shell job,
    say. Only a pid we started ourselves (own session, recorded in our state file) may be
    signalled by group."""
    s = _services(world, tmp_path)
    world.up["comfyui"] = "ready"
    plain: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        plain.append(pid)
        world.up.pop("comfyui", None)

    def fake_run(cmd, **kw):
        if list(cmd[:2]) == ["pgrep", "-f"]:
            return subprocess.CompletedProcess(list(cmd), 0, "9931\n", "")
        return world.fake_run(cmd, **kw)

    monkeypatch.setattr(svc.os, "kill", fake_kill)
    monkeypatch.setattr(svc, "SUBPROCESS_RUN", fake_run)

    assert not (tmp_path / ".services" / "comfyui.json").exists()  # nothing we started
    s.stop("comfyui")

    assert plain == [9931]  # signalled exactly the process pgrep found
    assert world.killed == []  # and no process group at all


def test_a_gpu_that_fell_off_the_bus_is_named_rather_than_retried(monkeypatch) -> None:
    """Measured on vegaserv 2026-09-10 03:12: a PCIe AER uncorrectable error during an LTX-2.5
    22B generation, then `Xid 79, GPU has fallen off the bus` and `Xid 154, recovery action
    changed to Node Reboot Required`. Inside the pipeline that arrived as two unrelated bugs —
    `ComfyTransientError: All connection attempts failed` on one lane and SeedVR2's
    `SafetensorError: device cpu:0 is invalid` on the next — and a queue would have spent every
    remaining job finding out.
    """
    import subprocess

    import pytest

    from content_factory.media.video_generate import MockVideoBackend
    from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend
    from content_factory.workflows import stages as st
    from content_factory.workflows.blocked import BlockedError

    def smi(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd,
            9,
            "No devices were found\n",
            "Unable to determine the device handle for GPU0: 0000:01:00.0: Unknown Error\n",
        )

    # `gpu_is_gone` imports subprocess inside the function, so the real module is the seam.
    monkeypatch.setattr(subprocess, "run", smi)
    said = st.gpu_is_gone()
    assert "No devices were found" in said

    started: list[str] = []
    monkeypatch.setattr(
        "content_factory.services.local.ensure_service", lambda t: started.append(t)
    )
    with pytest.raises(BlockedError, match="Xid 79"):
        st._ensure_backend_ready(HiDreamReferenceEditBackend(), set())
    assert started == [], "nothing may be started on a card that is not there"

    # A backend that needs no local tenant is untouched, and so is a host with no nvidia-smi.
    st._ensure_backend_ready(MockVideoBackend(), set())

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", missing)
    assert st.gpu_is_gone() == ""  # an offline machine is not a broken one


def test_stop_waits_for_the_driver_to_give_the_card_back(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three-second sleep this replaces hard-locked the machine on 2026-09-12.

    `stop` returns once the tenant stops answering HTTP, which is the moment it closes its socket
    -- tens of GB of CUDA allocations still being torn down behind it. ComfyUI was started into
    that window, its CUDA init came back "unknown error ... Setting the available devices to be
    zero", and the box went down four seconds later with nothing in the journal.
    """
    from content_factory.services import local as local_mod

    readings = iter([1_000, 2_000, 9_000, 19_500])
    monkeypatch.setattr(local_mod, "FREE_VRAM_MIB", lambda: next(readings, 19_500))
    services = _services(world, tmp_path)
    before = world.clock
    services._wait_for_vram()
    # It polled while the number was still climbing rather than sleeping a fixed guess, and
    # returned as soon as the card was actually back.
    assert world.clock > before


def test_a_card_someone_else_partly_owns_does_not_stall_every_handover(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free VRAM that stops climbing short of the target is another owner, not a slow release.

    Without the settle rule this waits out the whole timeout on every handover, which would turn
    a rare hang into a guaranteed two-minute tax.
    """
    from content_factory.services import local as local_mod

    monkeypatch.setattr(local_mod, "FREE_VRAM_MIB", lambda: 12_000)  # never reaches 18 GB
    services = _services(world, tmp_path)
    before = world.clock
    services._wait_for_vram()
    waited = world.clock - before
    assert 0 < waited < services.cfg.vram_release_timeout_s


def test_a_machine_with_no_driver_does_not_wait(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CPU-only box has nothing to arbitrate and must not pay for the check."""
    from content_factory.services import local as local_mod

    monkeypatch.setattr(local_mod, "FREE_VRAM_MIB", lambda: None)
    services = _services(world, tmp_path)
    before = world.clock
    services._wait_for_vram()
    assert world.clock == before


def test_a_gpu_server_is_started_in_its_own_scope_not_the_shell_that_launched_it(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What died on 2026-09-13 was the operator's terminal, not the server that overran.

    ComfyUI loading Ideogram 4 reached 26.5 GB of RSS on a 31 GB box, and the cgroup the kernel
    named in the oom-kill was the wezterm scope the shell was in -- because a server started from
    a shell inherits that shell's scope and systemd kills per scope. In a scope of its own, the
    ceiling stops it getting that big and an overrun kills the allocator.
    """
    from content_factory.services import local as local_mod

    monkeypatch.setattr(local_mod, "SYSTEMD_RUN_AVAILABLE", lambda: True)
    services = _services(world, tmp_path)
    services.ensure("hidream")
    spawned = world.popen[0]
    assert spawned[0] == "systemd-run"
    assert "--scope" in spawned and "--user" in spawned
    assert "MemoryMax=22G" in spawned
    # Swap is 3.7 GB on this box and filling it is how the desktop stops responding before the
    # kill lands, so the tenant is denied it outright.
    assert "MemorySwapMax=0" in spawned
    # The real command still follows, unaltered.
    assert spawned[-1].endswith("skills/image/hidream/server.py")


def test_a_box_without_user_systemd_still_starts_the_server(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling is a safety net, not a dependency: a container has no user manager to ask."""
    from content_factory.services import local as local_mod

    monkeypatch.setattr(local_mod, "SYSTEMD_RUN_AVAILABLE", lambda: False)
    services = _services(world, tmp_path)
    services.ensure("hidream")
    assert world.popen[0][:3] == ["uv", "run", "--project"]


def test_the_ceiling_can_be_turned_off(
    world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from content_factory.services import local as local_mod

    monkeypatch.setattr(local_mod, "SYSTEMD_RUN_AVAILABLE", lambda: True)
    services = _services(world, tmp_path, confine_tenants=False)
    services.ensure("hidream")
    assert world.popen[0][0] != "systemd-run"
