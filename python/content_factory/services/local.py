"""Local GPU services the pipeline manages itself."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

from content_factory.config import get_settings
from content_factory.config.settings import LocalServicesSettings
from content_factory.schemas.comfyui import ComfyWorkflowPackage, RequiredModel

REPO_ROOT = Path(__file__).resolve().parents[3]
Tenant = Literal["hidream", "comfyui", "ideogram4"]
TENANTS: tuple[Tenant, ...] = ("hidream", "comfyui", "ideogram4")

# GGUF loaders (ComfyUI-GGUF) historically read from the legacy folders; the packages declare the
# modern ones. Link GGUF files into both so either loader version finds them.
_GGUF_ALIASES = {"diffusion_models": "unet", "text_encoders": "clip"}

SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


def _query_free_vram_mib() -> int | None:
    """Free VRAM in MiB, or ``None`` where there is no ``nvidia-smi`` to ask."""
    try:
        out = subprocess.check_output(
            # Found on PATH, as every other GPU check in this repo does it: pinning /usr/bin
            # would break the driver's alternatives symlink.
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],  # noqa: S607
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return int(out.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


FREE_VRAM_MIB: Callable[[], int | None] = _query_free_vram_mib
"""Seam: the tests decide how full the card is without owning one."""


def _systemd_run_available() -> bool:
    """Is there a *user* systemd instance that can hold a transient scope for us?"""
    try:
        return (
            SUBPROCESS_RUN(
                ["systemd-run", "--user", "--scope", "--quiet", "--collect", "true"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


SYSTEMD_RUN_AVAILABLE: Callable[[], bool] = _systemd_run_available
"""Seam: the tests decide whether the box has a user systemd instance."""
SUBPROCESS_POPEN: Callable[..., subprocess.Popen] = subprocess.Popen


def services_dir(repo_root: Path = REPO_ROOT) -> Path:
    """Where everything this machine started records its handles: service pids and logs."""
    return Path(os.environ.get("CF_SERVICES_DIR") or repo_root / ".services")


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class LinkResult:
    linked: tuple[str, ...]
    present: tuple[str, ...]
    missing: tuple[str, ...]


def known_packages() -> tuple[ComfyWorkflowPackage, ...]:
    """Every ComfyUI package the stages can run; their RequiredModels are what gets linked."""
    from content_factory.media.ltx_packages import ltx_i2v_package
    from content_factory.media.wan_packages import wan_animate2_pose_package

    return (ltx_i2v_package(), wan_animate2_pose_package())


def link_required_models(
    models: Iterable[RequiredModel], *, comfy_models_dir: Path, weight_store: Path
) -> LinkResult:
    """Symlink each required weight from the store into ComfyUI's folder for it."""
    linked: list[str] = []
    present: list[str] = []
    missing: list[str] = []
    for m in models:
        folder = m.relative_path.removeprefix("models/")
        targets = [comfy_models_dir / folder / m.filename]
        if m.filename.endswith(".gguf") and folder in _GGUF_ALIASES:
            targets.append(comfy_models_dir / _GGUF_ALIASES[folder] / m.filename)
        source = _find_in_store(weight_store, m.filename)
        for target in targets:
            rel = f"{target.parent.name}/{target.name}"
            if target.exists() or target.is_symlink():
                present.append(rel)
                continue
            if source is None:
                missing.append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
            linked.append(rel)
    return LinkResult(tuple(linked), tuple(present), tuple(missing))


def _find_in_store(store: Path, filename: str) -> Path | None:
    if not store.is_dir():
        return None
    hits = sorted(p for p in store.glob(f"*/**/{filename}") if p.is_file())
    hits += sorted(p for p in store.glob(f"*/{filename}") if p.is_file() and p not in hits)
    return hits[0] if hits else None


def _terminate(pid: int, *, own_group: bool) -> None:
    """SIGTERM one service process, signalling its whole group only when we own that group."""
    try:
        if own_group:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


class LocalServices:
    def __init__(
        self,
        settings: LocalServicesSettings | None = None,
        *,
        hidream_endpoint: str | None = None,
        comfy_endpoint: str | None = None,
        ideogram4_endpoint: str | None = None,
        state_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        app = get_settings()
        self.cfg = settings or app.local_services
        self.endpoints: dict[Tenant, str] = {
            "hidream": hidream_endpoint or app.image_sequences.hidream_endpoint,
            "comfyui": comfy_endpoint or app.comfyui.endpoint,
            "ideogram4": ideogram4_endpoint or app.image_sequences.ideogram4_endpoint,
        }
        self.state_dir = state_dir if state_dir is not None else services_dir(repo_root)
        self.repo_root = repo_root
        self._http = httpx.Client(timeout=5.0, transport=transport)
        self._sleep = sleep
        self._monotonic = monotonic

    def close(self) -> None:
        """Release the http client's connection pool."""
        self._http.close()

    def __enter__(self) -> LocalServices:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- health -------------------------------------------------------------------------------
    def ready(self, tenant: Tenant) -> bool:
        base = self.endpoints[tenant]
        try:
            if tenant in ("hidream", "ideogram4"):
                r = self._http.get(f"{base}/healthz")
                return r.status_code == 200 and bool(r.json().get("loaded"))
            r = self._http.get(f"{base}/system_stats")
            return r.status_code == 200
        except (httpx.HTTPError, ValueError):
            return False

    def responding(self, tenant: Tenant) -> bool:
        """Up in any state (HiDream answers /healthz while still loading)."""
        base = self.endpoints[tenant]
        path = "/healthz" if tenant in ("hidream", "ideogram4") else "/system_stats"
        try:
            return self._http.get(f"{base}{path}").status_code == 200
        except httpx.HTTPError:
            return False

    # -- other GPU tenants ---------------------------------------------------------------------
    def unload_ollama(self) -> tuple[str, ...]:
        """Ask Ollama to release every catalogued model, and say which it released."""
        from content_factory.models.catalog import default_catalog

        base = get_settings().providers.ollama.endpoint.rstrip("/")
        catalogued = {d.model_id for d in default_catalog() if d.provider == "ollama"}
        try:
            listing = self._http.get(f"{base}/api/ps")
            resident = {
                str(m.get("model") or m.get("name") or "")
                for m in (listing.json().get("models") or [])
            }
        except (httpx.HTTPError, ValueError):
            return ()

        # A resident tag may carry a ``:latest`` the catalogue spells implicitly, or the other way.
        def variants(name: str) -> set[str]:
            return {name, name.removesuffix(":latest"), f"{name.removesuffix(':latest')}:latest"}

        wanted = [
            m
            for m in sorted(catalogued)
            if variants(m) & {v for r in resident for v in variants(r)}
        ]
        released: list[str] = []
        for model in wanted:
            try:
                response = self._http.post(
                    f"{base}/api/generate", json={"model": model, "keep_alive": 0}
                )
            except httpx.HTTPError:
                return tuple(released)
            if response.status_code == 200:
                released.append(model)
        return tuple(released)

    # -- lifecycle ----------------------------------------------------------------------------
    def ensure(self, tenant: Tenant) -> str:
        """Return the tenant's endpoint once it is healthy, starting it."""
        endpoint = self.endpoints[tenant]
        if self.ready(tenant):
            return endpoint
        # Before anything is started: the text models come off the card.
        self.unload_ollama()
        if not self.cfg.auto_start:
            msg = (
                f"{tenant} is not running at {endpoint} and local_services.auto_start is off;"
                f" start it with: {' '.join(self.start_command(tenant))}"
            )
            raise ServiceError(msg)
        if self.cfg.exclusive_gpu:
            for other in TENANTS:
                if other != tenant and self.responding(other):
                    self.stop(other)
        if not self.responding(tenant):
            self.start(tenant)
        self._wait_ready(tenant)
        return endpoint

    def _wait_for_vram(self) -> None:
        """Block until the driver has actually given the card back."""
        free = FREE_VRAM_MIB()
        if free is None:
            return  # no driver to ask: a CPU-only box, and nothing to arbitrate
        deadline = self._monotonic() + self.cfg.vram_release_timeout_s
        settled_at: float | None = None
        previous = free
        while True:
            current = FREE_VRAM_MIB()
            if current is None:
                return
            if current >= self.cfg.vram_free_target_mib:
                return
            # Not at the target, but no longer climbing: something else legitimately owns the rest
            # of the card (another user, a desktop compositor).
            if current <= previous:
                settled_at = settled_at if settled_at is not None else self._monotonic()
                if self._monotonic() - settled_at >= self.cfg.vram_settle_s:
                    return
            else:
                settled_at = None
            previous = max(previous, current)
            if self._monotonic() > deadline:
                return  # a slow release is not grounds for failing the run; the next load reports
            self._sleep(self.cfg.poll_interval_s)

    def _confined(self, command: list[str]) -> list[str]:
        """Wrap a tenant in its own systemd scope with a memory ceiling, where systemd can."""
        if not self.cfg.confine_tenants:
            return command
        ceiling = self.cfg.tenant_memory_max_gib
        if ceiling <= 0 or not SYSTEMD_RUN_AVAILABLE():
            return command
        return [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit=cf-{self.__class__.__name__.lower()}-{os.getpid()}-{len(command)}",
            "-p",
            f"MemoryMax={ceiling}G",
            # Swap is 3.7 GB here and filling it is how the desktop stops responding before the
            # kill lands. Denying the tenant swap entirely keeps the failure fast and local.
            "-p",
            "MemorySwapMax=0",
            *command,
        ]

    def start_command(self, tenant: Tenant) -> list[str]:
        if tenant == "hidream":
            skill = self.repo_root / self.cfg.hidream_skill_dir
            return ["uv", "run", "--project", str(skill), "python", str(skill / "server.py")]
        if tenant == "ideogram4":
            skill = self.repo_root / self.cfg.ideogram4_skill_dir
            return ["uv", "run", "--project", str(skill), "python", str(skill / "server.py")]
        workspace = self.comfy_workspace()
        python = workspace / ".venv" / "bin" / "python"
        url = urlparse(self.endpoints["comfyui"])
        return [
            str(python) if python.exists() else "python3",
            str(workspace / "main.py"),
            *self.cfg.comfy_extra_args,
            "--listen",
            url.hostname or "127.0.0.1",
            "--port",
            str(url.port or 8188),
        ]

    def start(self, tenant: Tenant) -> None:
        """Spawn the tenant detached (own session, log under .services/) and record its pid."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log = self.state_dir / f"{tenant}.log"
        env = dict(os.environ)
        if tenant == "hidream":
            env["CF_HIDREAM_MODEL_TYPE"] = self.cfg.hidream_model_type
            # The model needs ~19.4 GB of a 24 GB card, so whatever else the operator has open — a
            # game, a browser, another project's voice server.
            env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            env["CF_HIDREAM_PORT"] = str(urlparse(self.endpoints["hidream"]).port or 8801)
            # An adapter is part of what the server IS, so it is set at spawn and never per request.
            if self.cfg.hidream_lora:
                lora = Path(self.cfg.hidream_lora).expanduser()
                if not lora.is_absolute():
                    lora = self.repo_root / lora
                env["CF_HIDREAM_LORA"] = str(lora)
                env["CF_HIDREAM_LORA_MULTIPLIER"] = str(self.cfg.hidream_lora_multiplier)
            cwd = self.repo_root
        elif tenant == "ideogram4":
            # Same fragmentation argument as HiDream: group offloading streams leaf modules on and
            # off the card every step.
            env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            env["CF_IDEOGRAM4_PORT"] = str(urlparse(self.endpoints["ideogram4"]).port or 8802)
            cwd = self.repo_root
        else:
            if self.cfg.link_models:
                result = self.link_models()
                (self.state_dir / "comfy-links.json").write_text(
                    json.dumps(result.__dict__, indent=1, sort_keys=True)
                )
            cwd = self.comfy_workspace()
        with log.open("ab") as fh:
            proc = SUBPROCESS_POPEN(
                self._confined(self.start_command(tenant)),
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(cwd),
                start_new_session=True,
            )
        self._write_state(tenant, {"pid": proc.pid, "started_at": time.time()})

    def stop(self, tenant: Tenant) -> None:
        """Stop a tenant we (or an operator) started; waits until it stops answering."""
        pid = self._read_state(tenant).get("pid")
        if pid:
            pids = [int(pid)]
            _terminate(pids[0], own_group=True)  # started by us, in its own session
        else:
            pids = self._find_pids(tenant)
            for p in pids:
                _terminate(p, own_group=False)  # found by pattern; group ownership unknown
        if tenant == "comfyui" and not pids:
            # an operator's `comfy launch --background` is tracked by comfy-cli, not by us
            SUBPROCESS_RUN(
                [self.cfg.comfy_bin, "--skip-prompt", "stop"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        deadline = self._monotonic() + 120
        while self.responding(tenant):
            if self._monotonic() > deadline:
                msg = f"{tenant} did not stop within 120 s"
                raise ServiceError(msg)
            self._sleep(self.cfg.poll_interval_s)
        state = self.state_dir / f"{tenant}.json"
        if state.exists():
            state.unlink()
        self._wait_for_vram()

    def link_models(self) -> LinkResult:
        return link_required_models(
            [m for pkg in known_packages() for m in pkg.required_models],
            comfy_models_dir=self.comfy_models_dir(),
            weight_store=Path(self.cfg.weight_store).expanduser(),
        )

    def comfy_models_dir(self) -> Path:
        if self.cfg.comfy_models_dir:
            return Path(self.cfg.comfy_models_dir).expanduser()
        return self.comfy_workspace() / "models"

    def comfy_workspace(self) -> Path:
        if self.cfg.comfy_workspace:
            return Path(self.cfg.comfy_workspace).expanduser()
        proc = SUBPROCESS_RUN(
            [self.cfg.comfy_bin, "--skip-prompt", "which"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            return Path(data["data"]["workspace_path"])
        except (ValueError, KeyError, IndexError) as exc:
            msg = (
                f"could not resolve the ComfyUI workspace from `comfy which`: {proc.stdout[-300:]}"
            )
            raise ServiceError(msg) from exc

    # -- internals ----------------------------------------------------------------------------
    def _wait_ready(self, tenant: Tenant) -> None:
        deadline = self._monotonic() + self.cfg.startup_timeout_s
        while not self.ready(tenant):
            if self._monotonic() > deadline:
                msg = (
                    f"{tenant} did not become healthy within {self.cfg.startup_timeout_s} s;"
                    f" see {self.state_dir / (tenant + '.log')}"
                )
                raise ServiceError(msg)
            self._sleep(self.cfg.poll_interval_s)

    def _write_state(self, tenant: Tenant, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / f"{tenant}.json").write_text(json.dumps(data, indent=1, sort_keys=True))

    def _read_state(self, tenant: Tenant) -> dict:
        path = self.state_dir / f"{tenant}.json"
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            return {}

    def _find_pids(self, tenant: Tenant) -> list[int]:
        pattern = (
            f"{self.cfg.hidream_skill_dir}/server.py" if tenant == "hidream" else "ComfyUI/main.py"
        )
        proc = SUBPROCESS_RUN(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return [int(x) for x in proc.stdout.split() if x.isdigit()]


def ensure_service(tenant: Tenant) -> str:
    """Stage entry point: the tenant's endpoint, started if needed and allowed."""
    with LocalServices() as services:
        return services.ensure(tenant)


def free_the_gpu(*, unload_ollama: bool = True) -> dict[str, object]:
    """Evict every GPU tenant this module knows about, and report what was evicted."""
    with LocalServices() as services:
        stopped: list[str] = []
        for tenant in TENANTS:
            if services.responding(tenant):
                services.stop(tenant)
                stopped.append(tenant)
        released = services.unload_ollama() if unload_ollama else ()
    return {"stopped": stopped, "ollama_unloaded": list(released)}
