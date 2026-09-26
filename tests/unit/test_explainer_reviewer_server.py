"""The vLLM recipe as code: the argv it produces, the pid-file stop, and the busy-card refusal."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from content_factory.explainer.reviewer_server import (
    ReviewerServerConfig,
    ReviewerServerError,
    refuse_if_gpu_busy,
    stop,
)

UNUSED_PORT = 1


def test_config_renders_the_measured_flags_as_an_argument_array(tmp_path: Path) -> None:
    config = ReviewerServerConfig(model_dir=tmp_path / "model", vllm_bin=tmp_path / "vllm")
    argv = config.argv()
    assert argv[:3] == [str(tmp_path / "vllm"), "serve", str(tmp_path / "model")]
    pairs = dict(zip(argv[3::2], argv[4::2], strict=False))
    assert pairs["--served-model-name"] == "qwen3.8-27b-int4"
    assert pairs["--port"] == "8011"
    assert pairs["--max-model-len"] == "4096"
    assert pairs["--gpu-memory-utilization"] == "0.84"
    assert pairs["--kv-cache-dtype"] == "bfloat16"
    assert pairs["--limit-mm-per-prompt"] == '{"image": 2, "video": 1}'
    assert pairs["--mm-processor-kwargs"] == '{"max_pixels": 409600, "fps": 1, "max_frames": 8}'
    assert pairs["--max-num-seqs"] == "1"
    assert pairs["--max-num-batched-tokens"] == "2048"
    assert pairs["--attention-config"] == '{"backend": "TRITON_ATTN"}'
    assert "--enforce-eager" in argv and "--trust-remote-code" in argv
    assert config.env()["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert config.base_url == "http://127.0.0.1:8011/v1"


def test_stop_signals_only_the_recorded_vllm_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / "vllm.pid"
    pid_file.write_text("4242\n")
    killed: list[tuple[int, int]] = []
    alive = {"4242": 2}

    def cmdline(pid: int) -> str:
        if alive["4242"] > 0:
            alive["4242"] -= 1
            return "/venv/bin/python vllm serve /models/x"
        return ""

    stopped = stop(
        pid_file,
        kill=lambda pid, sig: killed.append((pid, sig)),
        cmdline=cmdline,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    assert stopped == 4242
    assert killed == [(4242, signal.SIGTERM)]
    assert not pid_file.exists()


def test_stop_refuses_a_pid_that_is_no_longer_vllm(tmp_path: Path) -> None:
    pid_file = tmp_path / "vllm.pid"
    pid_file.write_text("4242\n")
    killed: list[int] = []
    assert (
        stop(pid_file, kill=lambda pid, _sig: killed.append(pid), cmdline=lambda _p: "bash") is None
    )
    assert killed == [] and not pid_file.exists()


def test_stop_without_a_pid_file_is_a_no_op(tmp_path: Path) -> None:
    assert stop(tmp_path / "missing.pid", kill=lambda *_: None) is None


def test_refusal_below_eighteen_gb_free_when_no_reviewer_answers() -> None:
    config = ReviewerServerConfig(port=UNUSED_PORT)
    with pytest.raises(ReviewerServerError, match="17000 MiB"):
        refuse_if_gpu_busy(config, free_mib=lambda: 17_000)
    refuse_if_gpu_busy(config, free_mib=lambda: 18_000)
    refuse_if_gpu_busy(config, free_mib=lambda: None)
