"""One-click install: what it runs, where files land, and what it links afterwards.

Nothing here spawns a process or touches the network — the runner is injected and jobs run inline,
so the assertions are about the argument arrays and the filesystem effects. The interesting cases
are the ones the operator hits: a family already on disk (link, do not re-download), a gated repo
(say so instead of failing anonymously), a transfer that finishes without producing the declared
files (fail, do not report success), and the links that make a downloaded weight usable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from content_factory.config import Settings
from content_factory.models.weight_install import (
    InstallError,
    RunResult,
    WeightInstaller,
    ensure_index_link,
    package_status,
)
from content_factory.models.weights import (
    HuggingFaceSource,
    ProvidedFile,
    ReleaseSource,
    WeightPackage,
)

PACKAGE = WeightPackage(
    key="demo-family",
    name="Demo family",
    purpose="A family that exists only in this test",
    store_dir="demo-family",
    license="mit",
    approx_bytes=2048,
    provides=(
        ProvidedFile(
            store_rel="diffusion_models/demo.gguf",
            comfy_folder="models/diffusion_models",
            min_bytes=8,
        ),
        ProvidedFile(store_rel="vae/demo-vae.safetensors", comfy_folder="models/vae", min_bytes=8),
    ),
    hf=(
        HuggingFaceSource(
            repo_id="acme/demo",
            revision="0" * 40,
            files=("demo.gguf",),
            dest_subdir="diffusion_models",
        ),
        HuggingFaceSource(
            repo_id="acme/demo-extras",
            revision="1" * 40,
            files=("split_files/vae/demo-vae.safetensors",),
            strip_prefix="split_files",
        ),
    ),
    index_category="video_generation",
    index_name="Demo-Family",
)


class FakeRunner:
    """Records argv and writes whatever files the scripted response says landed."""

    def __init__(
        self,
        *,
        writes: dict[int, list[tuple[str, int]]] | None = None,
        results: dict[int, RunResult] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], Path | None]] = []
        self.envs: list[dict[str, str]] = []
        self.writes = writes or {}
        self.results = results or {}

    def __call__(
        self,
        argv: list[str],
        cwd: Path | None,
        log: Path | None,
        extra_env: Mapping[str, str] | None = None,
    ) -> RunResult:
        index = len(self.calls)
        self.calls.append((argv, cwd))
        self.envs.append(dict(extra_env or {}))
        dest = Path(argv[argv.index("--local-dir") + 1]) if "--local-dir" in argv else None
        for rel, size in self.writes.get(index, []):
            target = (dest or Path(argv[argv.index("-o") + 1]).parent) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * size)
        return self.results.get(index, RunResult(0, "ok"))


def installer(tmp_path: Path, runner: FakeRunner, *, packages=(PACKAGE,)) -> WeightInstaller:
    settings = Settings()
    settings = settings.model_copy(
        update={
            "local_services": settings.local_services.model_copy(
                update={
                    "weight_store": str(tmp_path / "store"),
                    "comfy_models_dir": str(tmp_path / "comfy" / "models"),
                }
            )
        }
    )
    inst = WeightInstaller(
        settings=settings,
        runner=runner,
        spawn=lambda work: work(),  # inline: the test wants the finished job, not a thread
        repo_root=tmp_path / "repo",
    )
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    return inst


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the demo family so the installer can look it up by key."""
    import content_factory.models.weight_install as module
    import content_factory.models.weights as weights

    monkeypatch.setattr(weights, "WEIGHT_PACKAGES", (PACKAGE,))
    monkeypatch.setattr(module, "WEIGHT_PACKAGES", (PACKAGE,))
    monkeypatch.setattr(weights, "SKILL_ENVS", ())


def test_install_downloads_pinned_sources_then_links(tmp_path: Path) -> None:
    runner = FakeRunner(
        writes={
            0: [("demo.gguf", 16)],
            1: [("split_files/vae/demo-vae.safetensors", 16)],
        }
    )
    inst = installer(tmp_path, runner)
    job = inst.start("demo-family")

    assert job.state == "complete", job.detail
    first, second = runner.calls[0][0], runner.calls[1][0]
    assert first[1:3] == ["download", "acme/demo"]
    assert "--revision" in first and first[first.index("--revision") + 1] == "0" * 40
    assert first[first.index("--local-dir") + 1].endswith("demo-family/diffusion_models")
    assert second[1:3] == ["download", "acme/demo-extras"]

    store = tmp_path / "store" / "demo-family"
    # strip_prefix flattened split_files/ away, which is what the loaders and links expect.
    assert (store / "vae" / "demo-vae.safetensors").is_file()
    assert not (store / "split_files").exists()
    # The index link the skills resolve through, and ComfyUI's own view of the same files.
    link = tmp_path / "repo" / "models" / "video_generation" / "Demo-Family"
    assert link.is_symlink() and link.resolve() == store.resolve()
    assert (tmp_path / "comfy" / "models" / "diffusion_models" / "demo.gguf").is_symlink()
    assert (tmp_path / "comfy" / "models" / "vae" / "demo-vae.safetensors").is_symlink()


def test_gguf_is_also_linked_into_the_legacy_loader_folder(tmp_path: Path) -> None:
    runner = FakeRunner(
        writes={0: [("demo.gguf", 16)], 1: [("split_files/vae/demo-vae.safetensors", 16)]}
    )
    inst = installer(tmp_path, runner)
    inst.start("demo-family")
    assert (tmp_path / "comfy" / "models" / "unet" / "demo.gguf").is_symlink()


def test_already_installed_does_not_download_but_does_link(tmp_path: Path) -> None:
    store = tmp_path / "store" / "demo-family"
    (store / "diffusion_models").mkdir(parents=True)
    (store / "diffusion_models" / "demo.gguf").write_bytes(b"x" * 16)
    (store / "vae").mkdir(parents=True)
    (store / "vae" / "demo-vae.safetensors").write_bytes(b"x" * 16)
    runner = FakeRunner()
    inst = installer(tmp_path, runner)

    job = inst.start("demo-family")

    assert job.state == "already_installed"
    assert runner.calls == []  # nothing was fetched
    assert (tmp_path / "repo" / "models" / "video_generation" / "Demo-Family").is_symlink()


def test_a_gated_repo_reports_needs_access_with_something_to_do(tmp_path: Path) -> None:
    runner = FakeRunner(
        results={0: RunResult(1, "401 Client Error: Cannot access gated repo for url ...")}
    )
    inst = installer(tmp_path, runner)
    job = inst.start("demo-family")
    assert job.state == "needs_access"
    assert "gated" in job.detail.lower() and "HF_TOKEN" in job.detail
    assert len(runner.calls) == 1  # stopped at the refusal, did not try the next source


def test_a_transfer_that_lands_nothing_fails_instead_of_claiming_success(tmp_path: Path) -> None:
    runner = FakeRunner()  # returns 0 but writes no files
    inst = installer(tmp_path, runner)
    job = inst.start("demo-family")
    assert job.state == "failed"
    assert "still missing" in job.detail


def test_a_partial_transfer_is_not_present(tmp_path: Path) -> None:
    store = tmp_path / "store" / "demo-family"
    (store / "diffusion_models").mkdir(parents=True)
    (store / "diffusion_models" / "demo.gguf").write_bytes(b"x")  # below min_bytes
    status = package_status(PACKAGE, store=tmp_path / "store")
    assert status.state == "partial"
    assert [f.state for f in status.files] == ["partial", "missing"]


def test_release_assets_go_through_curl_to_a_part_file(tmp_path: Path) -> None:
    package = PACKAGE.model_copy(
        update={
            "hf": (),
            "provides": (ProvidedFile(store_rel="demo.pth", min_bytes=8),),
            "releases": (
                ReleaseSource(
                    url="https://github.com/acme/demo/releases/download/v1/demo.pth",
                    dest="demo.pth",
                ),
            ),
        }
    )
    runner = FakeRunner(writes={0: [("demo.pth.part", 16)]})
    inst = installer(tmp_path, runner)
    import content_factory.models.weight_install as module
    import content_factory.models.weights as weights

    for module_ref in (weights, module):
        module_ref.WEIGHT_PACKAGES = (package,)  # type: ignore[misc]
    job = inst.start("demo-family")
    assert job.state == "complete", job.detail
    argv = runner.calls[0][0]
    assert argv[0] == "curl" and "--retry" in argv
    assert (tmp_path / "store" / "demo-family" / "demo.pth").is_file()
    assert not (tmp_path / "store" / "demo-family" / "demo.pth.part").exists()


def test_manual_families_refuse_to_pretend(tmp_path: Path) -> None:
    package = PACKAGE.model_copy(update={"hf": (), "manual": "weights are on Google Drive"})
    import content_factory.models.weight_install as module
    import content_factory.models.weights as weights

    for module_ref in (weights, module):
        module_ref.WEIGHT_PACKAGES = (package,)  # type: ignore[misc]
    inst = installer(tmp_path, FakeRunner())
    with pytest.raises(InstallError, match="Google Drive"):
        inst.start("demo-family")


def test_relink_repairs_a_broken_index_link(tmp_path: Path) -> None:
    store = tmp_path / "store" / "demo-family"
    (store / "diffusion_models").mkdir(parents=True)
    (store / "diffusion_models" / "demo.gguf").write_bytes(b"x" * 16)
    (store / "vae").mkdir(parents=True)
    (store / "vae" / "demo-vae.safetensors").write_bytes(b"x" * 16)
    link = tmp_path / "repo" / "models" / "video_generation" / "Demo-Family"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "gone")
    inst = installer(tmp_path, FakeRunner())

    report = inst.relink()

    assert link.resolve() == store.resolve()
    assert any("Demo-Family" in entry for entry in report["index"])


def test_a_real_directory_in_the_index_is_never_replaced(tmp_path: Path) -> None:
    store = tmp_path / "store" / "demo-family"
    store.mkdir(parents=True)
    occupied = tmp_path / "repo" / "models" / "video_generation" / "Demo-Family"
    occupied.mkdir(parents=True)
    (occupied / "operator-file.txt").write_text("mine")
    action = ensure_index_link(PACKAGE, store=tmp_path / "store", repo_root=tmp_path / "repo")
    assert action == "occupied"
    assert (occupied / "operator-file.txt").is_file()


def test_unknown_keys_are_refused(tmp_path: Path) -> None:
    inst = installer(tmp_path, FakeRunner())
    with pytest.raises(InstallError, match="unknown"):
        inst.start("not-a-family")


def test_a_gated_repo_token_travels_in_the_environment_and_never_in_argv(tmp_path: Path) -> None:
    """A token in argv is readable by every process on the machine through /proc."""
    runner = FakeRunner(
        writes={0: [("demo.gguf", 16)], 1: [("split_files/vae/demo-vae.safetensors", 16)]}
    )
    inst = installer(tmp_path, runner)

    inst.start("demo-family", hf_token="hf_secretvalue")

    assert all("hf_secretvalue" not in " ".join(argv) for argv, _ in runner.calls)
    assert runner.envs[0] == {"HF_TOKEN": "hf_secretvalue"}
    # And it is not written into the job the API hands back to the browser.
    job = inst.job("demo-family")
    assert job is not None and "hf_secretvalue" not in json.dumps(job.as_dict())
