"""The reviewer's vLLM serving recipe as code: flags, start and stop by pid, readiness, identity."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = Path("/mnt/fast/models/qwen3.8-27b-int4")
DEFAULT_VLLM_BIN = REPO_ROOT / ".venvs" / "vllm" / "bin" / "vllm"
DEFAULT_STATE_DIR = REPO_ROOT / "output" / "explainer" / "reviewer"
# services/local.py's rule: 18 GB of the 3090's 24 must be free before a second transformer loads.
VRAM_FREE_TARGET_MIB = 18_000
# FlashInfer's sampler JIT-compiles with nvcc, which this box does not have (journal 2026-09-23).
SERVER_ENV = {"VLLM_USE_FLASHINFER_SAMPLER": "0"}


class ReviewerServerError(RuntimeError):
    """The server could not be started, reached or stopped; the message says what to do."""


@dataclass(frozen=True)
class ReviewerServerConfig:
    """The measured recipe (journal 2026-09-23): 17.71 GiB of weights, 11,264 KV tokens left."""

    model_dir: Path = DEFAULT_MODEL_DIR
    served_name: str = "qwen3.8-27b-int4"
    host: str = "127.0.0.1"
    port: int = 8011
    max_model_len: int = 4096
    gpu_memory_utilization: float = 0.84
    kv_cache_dtype: str = "bfloat16"
    max_images: int = 2
    max_videos: int = 1
    max_pixels: int = 409_600
    video_fps: int = 1
    max_frames: int = 8
    max_num_seqs: int = 1
    max_num_batched_tokens: int = 2048
    attention_backend: str = "TRITON_ATTN"
    enforce_eager: bool = True
    vllm_bin: Path = DEFAULT_VLLM_BIN
    state_dir: Path = DEFAULT_STATE_DIR

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "vllm.pid"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "vllm-serve.log"

    def argv(self) -> list[str]:
        args = [
            str(self.vllm_bin), "serve", str(self.model_dir),
            "--served-model-name", self.served_name,
            "--host", self.host,
            "--port", str(self.port),
            "--max-model-len", str(self.max_model_len),
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
            "--kv-cache-dtype", self.kv_cache_dtype,
            "--limit-mm-per-prompt", json.dumps(self.mm_limits()),
            "--mm-processor-kwargs", json.dumps(self.mm_processor_kwargs()),
            "--max-num-seqs", str(self.max_num_seqs),
            "--max-num-batched-tokens", str(self.max_num_batched_tokens),
            "--attention-config", json.dumps({"backend": self.attention_backend}),
            "--trust-remote-code",
        ]  # fmt: skip
        if self.enforce_eager:
            args.append("--enforce-eager")
        return args

    def mm_limits(self) -> dict[str, int]:
        return {"image": self.max_images, "video": self.max_videos}

    def mm_processor_kwargs(self) -> dict[str, int]:
        return {"max_pixels": self.max_pixels, "fps": self.video_fps, "max_frames": self.max_frames}

    def env(self) -> dict[str, str]:
        return {**os.environ, **SERVER_ENV}


DEFAULT_CONFIG = ReviewerServerConfig()


def _query_free_vram_mib() -> int | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],  # noqa: S607  PATH, as services/local.py
            text=True,
            timeout=15,
        )
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


FREE_VRAM_MIB: Callable[[], int | None] = _query_free_vram_mib
"""Seam: the tests decide how full the card is without owning one."""


def is_running(url: str, timeout_s: float = 3.0) -> bool:
    try:
        return httpx.get(f"{url.rstrip('/')}/models", timeout=timeout_s).status_code == 200
    except httpx.HTTPError:
        return False


def wait_ready(
    url: str,
    timeout_s: float,
    *,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + timeout_s
    while not is_running(url):
        if clock() >= deadline:
            msg = f"the reviewer server at {url} did not answer within {timeout_s:.0f} s"
            raise ReviewerServerError(msg)
        sleep(poll_s)


def refuse_if_gpu_busy(
    config: ReviewerServerConfig,
    *,
    free_mib: Callable[[], int | None] | None = None,
    target_mib: int = VRAM_FREE_TARGET_MIB,
) -> None:
    """A running reviewer counts as ours; any other tenant leaving under 18 GB free refuses."""
    if is_running(config.base_url):
        return
    free = (free_mib or FREE_VRAM_MIB)()
    if free is None or free >= target_mib:
        return
    msg = (
        f"only {free} MiB of VRAM free, the reviewer needs {target_mib} MiB; stop the other GPU "
        "tenant first (`just stop`), never run two GPU jobs on this box"
    )
    raise ReviewerServerError(msg)


def start(
    config: ReviewerServerConfig | None = None,
    *,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    """Spawn vLLM detached with its log and pid under state_dir; refuses a busy card."""
    config = config or DEFAULT_CONFIG
    if is_running(config.base_url):
        msg = f"a server already answers at {config.base_url}; use it or stop it by its pid file"
        raise ReviewerServerError(msg)
    refuse_if_gpu_busy(config)
    if not config.vllm_bin.exists():
        msg = f"{config.vllm_bin} is missing; the vLLM venv is documented in docs/setup.md"
        raise ReviewerServerError(msg)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    with config.log_file.open("ab") as log:
        proc = popen(
            config.argv(),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=config.env(),
            cwd=str(REPO_ROOT),
            start_new_session=True,
        )
    config.pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc


def _cmdline(pid: int) -> str:
    try:
        return (
            Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        )
    except OSError:
        return ""


def stop(
    pid_file: Path,
    *,
    timeout_s: float = 120.0,
    kill: Callable[[int, int], None] = os.kill,
    cmdline: Callable[[int], str] = _cmdline,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int | None:
    """SIGTERM the recorded pid only, after checking it is still a vllm process; never a pattern."""
    if not pid_file.is_file():
        return None
    text = pid_file.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        pid_file.unlink()
        return None
    pid = int(text)
    if "vllm" not in cmdline(pid):
        pid_file.unlink()
        return None
    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink()
        return None
    deadline = clock() + timeout_s
    while "vllm" in cmdline(pid):
        if clock() >= deadline:
            msg = f"vllm pid {pid} did not exit within {timeout_s:.0f} s after SIGTERM"
            raise ReviewerServerError(msg)
        sleep(1.0)
    pid_file.unlink()
    return pid


def describe_environment(config: ReviewerServerConfig | None = None) -> dict[str, str]:
    """What the identity records: vLLM version, checkpoint config sha, quantization, limits."""
    config = config or DEFAULT_CONFIG
    quant = _quantization(config.model_dir / "config.json")
    return {
        "vllm_version": _vllm_version(config),
        "model_dir": str(config.model_dir),
        "config_sha256": _sha256_or_empty(config.model_dir / "config.json"),
        "quantization": quant,
        "attention_backend": config.attention_backend,
        "kv_cache_dtype": config.kv_cache_dtype,
        "max_model_len": str(config.max_model_len),
        "max_images": str(config.max_images),
        "max_videos": str(config.max_videos),
        "max_pixels": str(config.max_pixels),
        "video_fps": str(config.video_fps),
        "max_frames": str(config.max_frames),
    }


def model_revision(env: dict[str, str]) -> str:
    """The ReviewerIdentity.model_revision string, within its 80 characters."""
    return f"vllm {env.get('vllm_version', '?')} cfg {env.get('config_sha256', '')[:16]}"[:80]


def _vllm_version(config: ReviewerServerConfig) -> str:
    try:
        response = httpx.get(f"http://{config.host}:{config.port}/version", timeout=3.0)
        if response.status_code == 200:
            return str(response.json().get("version", ""))
    except (httpx.HTTPError, ValueError):
        pass
    python = config.vllm_bin.with_name("python")
    if not python.exists():
        return ""
    completed = subprocess.run(  # noqa: S603  argument array, our venv's interpreter
        [str(python), "-c", "import importlib.metadata as m; print(m.version('vllm'))"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _quantization(config_json: Path) -> str:
    try:
        config = json.loads(config_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    quant = config.get("quantization_config") or {}
    method = str(quant.get("quant_method", ""))
    groups = quant.get("config_groups") or {}
    bits = next(
        (g.get("weights", {}).get("num_bits") for g in groups.values() if isinstance(g, dict)), None
    )
    scheme = f"W{bits}A16 " if isinstance(bits, int) else ""
    return f"{scheme}{method}".strip()[:40]


def _sha256_or_empty(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
