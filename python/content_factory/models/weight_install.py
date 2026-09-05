"""Installs a declared weight family from the browser, and links it where the code looks for it.

The registry (:mod:`content_factory.models.weights`) says what a family is and where it comes
from; this module is the only thing that acts on it. One click in the Models page becomes:

1. ``hf download <repo> <files> --revision <sha> --local-dir <store>/<family>`` per pinned source,
   or a pinned release asset over https, or ``uv sync`` for a skill environment.
2. flatten a declared ``strip_prefix`` (Comfy-Org publishes under ``split_files/``),
3. create the ``<repo>/models/<category>/<Name>`` index symlink,
4. symlink every file that ComfyUI loads into ComfyUI's own ``models/<folder>`` tree.

Steps 3 and 4 are the difference between a downloaded weight and a usable one: the skills resolve
their weights through the index link and ComfyUI only sees what is inside its own models tree, so
an installer that stops after the transfer leaves 30 GB on disk that nothing can find. They are
also idempotent and safe to run on their own, which is what the relink call is for.

What this module will not do: download from anywhere but the pinned sources (the argument arrays
are built from validated registry fields, never from request bodies or model output), overwrite a
file that is already there, or claim success it has not verified — a job ends ``complete`` only
when every file the family declares is present at its declared minimum size.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from content_factory.config import Settings, get_settings
from content_factory.models.weights import (
    WEIGHT_PACKAGES,
    HuggingFaceSource,
    ProvidedFile,
    ReleaseSource,
    SkillEnv,
    WeightPackage,
    package_by_key,
    skill_env_by_key,
)
from content_factory.schemas.comfyui import RequiredModel
from content_factory.services.local import link_required_models

REPO_ROOT = Path(__file__).resolve().parents[3]

JobState = Literal["running", "complete", "failed", "already_installed", "needs_access"]
FileState = Literal["present", "missing", "partial"]

# Recognisable Hub refusals. A gated repo answers 401/403 and there is nothing to retry: the
# operator has to accept the terms (or be granted access), so the job says that instead of
# reporting a generic failure the UI cannot act on.
_ACCESS_MARKERS = (
    "401 client error",
    "403 client error",
    "gated repo",
    "access to model",
    "awaiting a review",
    "authorization",
)


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class RunResult:
    returncode: int
    output: str


@dataclass(frozen=True)
class FileStatus:
    store_rel: str
    filename: str
    state: FileState
    size_bytes: int
    comfy_folder: str
    comfy_linked: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "store_rel": self.store_rel,
            "filename": self.filename,
            "state": self.state,
            "size_bytes": self.size_bytes,
            "comfy_folder": self.comfy_folder,
            "comfy_linked": self.comfy_linked,
        }


@dataclass(frozen=True)
class PackageStatus:
    """What is true on disk about one family, right now."""

    package: WeightPackage
    state: Literal["ready", "partial", "absent", "manual"]
    files: tuple[FileStatus, ...]
    bytes_on_disk: int
    store_path: str
    index_path: str
    index_state: Literal["ok", "missing", "broken", "not_indexed"]

    def as_dict(self) -> dict[str, Any]:
        p = self.package
        return {
            "key": p.key,
            "name": p.name,
            "purpose": p.purpose,
            "license": p.license,
            "approx_bytes": p.approx_bytes,
            "installable": p.installable,
            "manual": p.manual,
            "gating": p.gating,
            "caveat": p.caveat,
            "sources": [
                {"repo_id": s.repo_id, "revision": s.revision, "gated": s.gated, "note": s.note}
                for s in p.hf
            ]
            + [{"url": r.url, "revision": "", "gated": "no", "note": ""} for r in p.releases],
            "build_command": list(p.build_command),
            "state": self.state,
            "files": [f.as_dict() for f in self.files],
            "bytes_on_disk": self.bytes_on_disk,
            "store_path": self.store_path,
            "index_path": self.index_path,
            "index_state": self.index_state,
        }


@dataclass(frozen=True)
class SkillEnvStatus:
    env: SkillEnv
    state: Literal["ready", "absent"]
    path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.env.key,
            "skill": self.env.skill,
            "name": self.env.name,
            "purpose": self.env.purpose,
            "caveat": self.env.caveat,
            "state": self.state,
            "path": self.path,
        }


@dataclass
class InstallStep:
    label: str
    state: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "state": self.state, "detail": self.detail}


@dataclass
class InstallJob:
    key: str
    kind: Literal["weights", "skill_env"]
    name: str
    state: JobState
    detail: str = ""
    steps: list[InstallStep] = field(default_factory=list)
    bytes_expected: int = 0
    bytes_done: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "steps": [s.as_dict() for s in self.steps],
            "bytes_expected": self.bytes_expected,
            "bytes_done": self.bytes_done,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# --- paths and tools ---------------------------------------------------------------------------


def weight_store(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.local_services.weight_store).expanduser()


def index_root(repo_root: Path = REPO_ROOT) -> Path:
    """``<repo>/models`` — the category-sorted symlink index, never a weight store itself."""
    return repo_root / "models"


def comfy_models_dir(settings: Settings | None = None) -> Path | None:
    """Where ComfyUI looks for models, when this host has a ComfyUI workspace configured."""
    settings = settings or get_settings()
    configured = settings.local_services.comfy_models_dir
    if configured:
        return Path(configured).expanduser()
    workspace = settings.local_services.comfy_workspace or settings.comfyui.workspace
    return Path(workspace).expanduser() / "models" if workspace else None


def hf_binary(repo_root: Path = REPO_ROOT) -> str:
    """The downloader's own venv first (that is where the download scripts put it), then PATH."""
    local = repo_root / ".venvs" / "download" / "bin" / "hf"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("hf") or "hf"


def store_space(store: Path) -> tuple[int, int]:
    """(free, total) bytes on the filesystem holding the weight store, 0 when it cannot be read.

    Shown next to a family's size because these downloads are tens of gigabytes: an operator
    about to click Install on 41 GB should be able to see whether 41 GB is there.
    """
    probe = store
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return (0, 0)
    return (usage.free, usage.total)


def _dir_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".cache"]  # hf's own resume metadata
        for name in filenames:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


# --- status ------------------------------------------------------------------------------------


def _file_status(
    package: WeightPackage, provided: ProvidedFile, store: Path, comfy: Path | None
) -> FileStatus:
    path = store / package.store_dir / provided.store_rel
    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        size = 0
    state: FileState = (
        "present" if size >= provided.min_bytes else ("partial" if size else "missing")
    )
    linked = False
    if provided.comfy_folder and comfy is not None:
        target = comfy / provided.comfy_folder.removeprefix("models/") / provided.filename
        linked = target.is_symlink() or target.is_file()
    return FileStatus(
        store_rel=provided.store_rel,
        filename=provided.filename,
        state=state,
        size_bytes=size,
        comfy_folder=provided.comfy_folder,
        comfy_linked=linked,
    )


def package_status(
    package: WeightPackage,
    *,
    store: Path,
    comfy: Path | None = None,
    repo_root: Path = REPO_ROOT,
) -> PackageStatus:
    files = tuple(_file_status(package, f, store, comfy) for f in package.provides)
    present = [f for f in files if f.state == "present"]
    started = [f for f in files if f.state in ("present", "partial")]
    if len(present) == len(files):
        state: Literal["ready", "partial", "absent", "manual"] = "ready"
    elif started:
        # Anything on disk that is not yet a usable weight reads as partial, truncated files
        # included: an interrupted 30 GB transfer must not present itself as "nothing here".
        state = "partial"
    else:
        state = "manual" if package.manual else "absent"

    index_path = ""
    index_state: Literal["ok", "missing", "broken", "not_indexed"] = "not_indexed"
    if package.index_category:
        link = index_root(repo_root) / package.index_category / package.index_name
        index_path = str(link)
        if link.is_symlink():
            index_state = "ok" if link.resolve().is_dir() else "broken"
        elif link.is_dir():
            index_state = "ok"
        else:
            index_state = "missing"
    return PackageStatus(
        package=package,
        state=state,
        files=files,
        bytes_on_disk=_dir_bytes(store / package.store_dir),
        store_path=str(store / package.store_dir),
        index_path=index_path,
        index_state=index_state,
    )


def skill_env_status(env: SkillEnv, *, repo_root: Path = REPO_ROOT) -> SkillEnvStatus:
    python = repo_root / env.skill / ".venv" / "bin" / "python"
    return SkillEnvStatus(
        env=env, state="ready" if python.is_file() else "absent", path=str(repo_root / env.skill)
    )


def catalog_status(
    *,
    settings: Settings | None = None,
    repo_root: Path = REPO_ROOT,
) -> tuple[tuple[PackageStatus, ...], tuple[SkillEnvStatus, ...]]:
    settings = settings or get_settings()
    store = weight_store(settings)
    comfy = comfy_models_dir(settings)
    packages = tuple(
        package_status(p, store=store, comfy=comfy, repo_root=repo_root) for p in WEIGHT_PACKAGES
    )
    envs = tuple(skill_env_status(e, repo_root=repo_root) for e in weights_skill_envs())
    return packages, envs


def weights_skill_envs() -> tuple[SkillEnv, ...]:
    from content_factory.models.weights import SKILL_ENVS

    return SKILL_ENVS


# --- linking -----------------------------------------------------------------------------------


def ensure_index_link(package: WeightPackage, *, store: Path, repo_root: Path = REPO_ROOT) -> str:
    """Create ``<repo>/models/<category>/<Name> -> <store>/<family>``. Returns what it did."""
    if not package.index_category:
        return "not_indexed"
    target = store / package.store_dir
    if not target.is_dir():
        return "no_target"
    link = index_root(repo_root) / package.index_category / package.index_name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.readlink() == target:
            return "present"
        # A link pointing at a different or vanished directory is worse than none: repoint it.
        link.unlink()
    elif link.exists():
        return "occupied"  # a real directory: never replaced from here
    link.symlink_to(target)
    return "linked"


def _required_models(package: WeightPackage, store: Path) -> list[RequiredModel]:
    """The ComfyUI-visible files as RequiredModels, so linking reuses services.local's rules
    (existing files untouched, GGUF also linked into the legacy loader folders)."""
    source = (
        f"https://huggingface.co/{package.hf[0].repo_id}"
        if package.hf
        else (package.releases[0].url if package.releases else "local build")
    )
    out: list[RequiredModel] = []
    for provided in package.provides:
        if not provided.comfy_folder:
            continue
        if not (store / package.store_dir / provided.store_rel).is_file():
            continue
        out.append(
            RequiredModel(
                filename=provided.filename,
                relative_path=provided.comfy_folder,
                source_url=source,
                license=package.license,
            )
        )
    return out


def link_into_comfy(package: WeightPackage, *, store: Path, comfy: Path | None) -> tuple[str, ...]:
    if comfy is None:
        return ()
    models = _required_models(package, store)
    if not models:
        return ()
    result = link_required_models(models, comfy_models_dir=comfy, weight_store=store)
    return result.linked


# --- the installer -----------------------------------------------------------------------------

Runner = Callable[[list[str], Path | None, Path | None, Mapping[str, str] | None], RunResult]
"""(argv, cwd, log_path, extra_env) -> result. Injected so tests never spawn a process."""

Spawn = Callable[[Callable[[], None]], None]
"""How a job's work is started; the default detaches a thread, tests run it inline."""


def default_runner(
    argv: list[str],
    cwd: Path | None,
    log_path: Path | None,
    extra_env: Mapping[str, str] | None = None,
) -> RunResult:
    env = dict(os.environ)
    # hf-transfer is what makes a 30 GB pull finish in minutes; harmless when not installed.
    env.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    # A Hugging Face token, when the operator has stored one: it reaches the transfer only as
    # this subprocess's environment, never as an argument (argv is world-readable in /proc).
    env.update(extra_env or {})
    completed = subprocess.run(  # noqa: S603 — argv built from validated registry fields
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"$ {' '.join(argv)}\n{output}\n")
        except OSError:
            pass
    return RunResult(completed.returncode, output)


def _default_spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class WeightInstaller:
    """One job per family, keyed by registry key. Poll-on-read; no polling threads."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        runner: Runner = default_runner,
        spawn: Spawn = _default_spawn,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self._settings = settings or get_settings()
        self._runner = runner
        self._spawn = spawn
        self._repo_root = repo_root
        self._jobs: dict[str, InstallJob] = {}
        self._lock = threading.Lock()
        # Set per call by start(); the token never enters a job record, a log line or an argv.
        self._hf_token: str | None = None

    # -- introspection
    @property
    def store(self) -> Path:
        return weight_store(self._settings)

    @property
    def comfy(self) -> Path | None:
        return comfy_models_dir(self._settings)

    def jobs(self) -> list[InstallJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.state == "running":
                job.bytes_done = self._job_bytes(job)
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def job(self, key: str) -> InstallJob | None:
        with self._lock:
            return self._jobs.get(key)

    def _job_bytes(self, job: InstallJob) -> int:
        package = package_by_key(job.key)
        return _dir_bytes(self.store / package.store_dir) if package else 0

    # -- starting
    def start(self, key: str, *, hf_token: str | None = None) -> InstallJob:
        """Begin (or return) the install of one family or skill env. Idempotent per key.

        ``hf_token`` is the operator's stored Hugging Face token, when there is one: gated
        repositories (LTX-2.5, Stable Audio, SAM 3.1) answer 401 without it.
        """
        if hf_token:
            self._hf_token = hf_token
        with self._lock:
            existing = self._jobs.get(key)
            if existing and existing.state == "running":
                return existing

        env = skill_env_by_key(key)
        if env is not None:
            return self._start_skill_env(env)
        package = package_by_key(key)
        if package is None:
            msg = f"unknown model package {key!r}"
            raise InstallError(msg)
        if not package.installable:
            msg = f"{package.key} has no automatic source: {package.manual}"
            raise InstallError(msg)
        return self._start_package(package)

    def _register(self, job: InstallJob) -> InstallJob:
        with self._lock:
            self._jobs[job.key] = job
        return job

    def _log_path(self, key: str) -> Path:
        return self._repo_root / ".services" / "model-install" / f"{key.replace('/', '_')}.log"

    def _start_package(self, package: WeightPackage) -> InstallJob:
        status = package_status(
            package, store=self.store, comfy=self.comfy, repo_root=self._repo_root
        )
        job = InstallJob(
            key=package.key,
            kind="weights",
            name=package.name,
            state="running",
            bytes_expected=package.approx_bytes,
            bytes_done=status.bytes_on_disk,
            log_path=str(self._log_path(package.key)),
        )
        if status.state == "ready":
            # Already on disk: the links are the only thing that can still be missing, and they
            # are cheap, so do them now rather than reporting "installed" over a broken link.
            job.steps = [InstallStep("link", "running")]
            self._register(job)
            self._finish_links(job, package)
            job.state = "already_installed"
            job.detail = "every declared file was already present"
            job.finished_at = time.time()
            return job

        job.steps = [InstallStep(f"download {source.repo_id}") for source in package.hf] + [
            InstallStep(f"fetch {release.dest}") for release in package.releases
        ]
        if package.build_command:
            job.steps.append(InstallStep("build assets"))
        job.steps.append(InstallStep("link"))
        self._register(job)
        self._spawn(lambda: self._run_package(job, package))
        return job

    def _start_skill_env(self, env: SkillEnv) -> InstallJob:
        job = InstallJob(
            key=env.key,
            kind="skill_env",
            name=env.name,
            state="running",
            steps=[InstallStep(f"uv sync {env.skill}")],
            log_path=str(self._log_path(env.key)),
        )
        self._register(job)
        self._spawn(lambda: self._run_skill_env(job, env))
        return job

    # -- the work itself
    def _run_package(self, job: InstallJob, package: WeightPackage) -> None:
        store_dir = self.store / package.store_dir
        try:
            step_index = 0
            for source in package.hf:
                step = job.steps[step_index]
                step_index += 1
                if not self._download_hf(job, step, package, source):
                    return
            for release in package.releases:
                step = job.steps[step_index]
                step_index += 1
                if not self._fetch_release(job, step, store_dir, release):
                    return
            if package.build_command:
                step = job.steps[step_index]
                step_index += 1
                step.state = "running"
                result = self._runner(
                    list(package.build_command), self._repo_root, Path(job.log_path), None
                )
                if result.returncode != 0:
                    step.state = "failed"
                    self._fail(job, _tail(result.output) or "build failed")
                    return
                step.state = "done"
            self._finish_links(job, package)
            self._verify(job, package)
        except OSError as err:  # a full disk, a vanished mount — say which
            self._fail(job, f"{type(err).__name__}: {err}")

    def _download_hf(
        self, job: InstallJob, step: InstallStep, package: WeightPackage, source: HuggingFaceSource
    ) -> bool:
        step.state = "running"
        dest = self.store / package.store_dir / source.dest_subdir
        dest.mkdir(parents=True, exist_ok=True)
        argv = [hf_binary(self._repo_root), "download", source.repo_id]
        argv += list(source.files)
        for pattern in source.include:
            argv += ["--include", pattern]
        argv += ["--revision", source.revision, "--local-dir", str(dest)]
        env = {"HF_TOKEN": self._hf_token} if self._hf_token else None
        result = self._runner(argv, self._repo_root, Path(job.log_path), env)
        if result.returncode != 0:
            lowered = result.output.lower()
            step.state = "failed"
            step.detail = _tail(result.output)
            if any(marker in lowered for marker in _ACCESS_MARKERS):
                job.state = "needs_access"
                job.detail = (
                    f"{source.repo_id} is gated. Accept its terms on Hugging Face while logged in"
                    " and set HF_TOKEN for this API, then install again."
                )
                job.finished_at = time.time()
                return False
            self._fail(job, step.detail or f"{source.repo_id} download failed")
            return False
        if source.strip_prefix:
            _flatten(dest / source.strip_prefix, dest)
        step.state = "done"
        job.bytes_done = self._job_bytes(job)
        return True

    def _fetch_release(
        self, job: InstallJob, step: InstallStep, store_dir: Path, release: ReleaseSource
    ) -> bool:
        step.state = "running"
        dest = store_dir / release.dest
        if dest.is_file() and dest.stat().st_size > 0:
            step.state = "skipped"
            step.detail = "already present"
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        argv = ["curl", "-fL", "--retry", "3", "-o", str(part), release.url]
        result = self._runner(argv, self._repo_root, Path(job.log_path), None)
        if result.returncode != 0 or not part.is_file():
            step.state = "failed"
            step.detail = _tail(result.output)
            self._fail(job, step.detail or f"{release.url} failed")
            return False
        part.replace(dest)
        step.state = "done"
        return True

    def _finish_links(self, job: InstallJob, package: WeightPackage) -> None:
        step = job.steps[-1]
        step.state = "running"
        index = ensure_index_link(package, store=self.store, repo_root=self._repo_root)
        linked = link_into_comfy(package, store=self.store, comfy=self.comfy)
        step.state = "done"
        step.detail = f"index {index}" + (f"; comfy {len(linked)} linked" if linked else "")

    def _verify(self, job: InstallJob, package: WeightPackage) -> None:
        status = package_status(
            package, store=self.store, comfy=self.comfy, repo_root=self._repo_root
        )
        job.bytes_done = status.bytes_on_disk
        if status.state == "ready":
            job.state = "complete"
            job.detail = f"{len(status.files)} files in {status.store_path}"
        else:
            short = [f.store_rel for f in status.files if f.state != "present"]
            self._fail(job, f"transfer finished but these are still missing: {', '.join(short)}")
        job.finished_at = time.time()

    def _run_skill_env(self, job: InstallJob, env: SkillEnv) -> None:
        step = job.steps[0]
        step.state = "running"
        result = self._runner(["uv", "sync"], self._repo_root / env.skill, Path(job.log_path), None)
        if result.returncode != 0:
            step.state = "failed"
            step.detail = _tail(result.output)
            self._fail(job, step.detail or "uv sync failed")
            return
        step.state = "done"
        status = skill_env_status(env, repo_root=self._repo_root)
        if status.state == "ready":
            job.state = "complete"
            job.detail = f"{env.skill}/.venv ready"
        else:
            self._fail(job, "uv sync reported success but the env has no python")
        job.finished_at = time.time()

    def _fail(self, job: InstallJob, detail: str) -> None:
        job.state = "failed"
        job.detail = detail
        job.finished_at = time.time()

    # -- repair
    def relink(self) -> dict[str, list[str]]:
        """Re-create the index and ComfyUI links for everything already on disk. Idempotent."""
        report: dict[str, list[str]] = {"index": [], "comfy": [], "skipped": []}
        for package in WEIGHT_PACKAGES:
            status = package_status(
                package, store=self.store, comfy=self.comfy, repo_root=self._repo_root
            )
            if status.state in ("absent", "manual"):
                report["skipped"].append(package.key)
                continue
            action = ensure_index_link(package, store=self.store, repo_root=self._repo_root)
            if action in ("linked", "present"):
                report["index"].append(f"{package.index_category}/{package.index_name}:{action}")
            linked = link_into_comfy(package, store=self.store, comfy=self.comfy)
            report["comfy"].extend(linked)
        return report


def _flatten(source: Path, dest: Path) -> None:
    """Move ``<dest>/<prefix>/*`` up into ``<dest>`` and drop the empty prefix directory."""
    if not source.is_dir() or source == dest:
        return
    for entry in list(source.iterdir()):
        target = dest / entry.name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        entry.replace(target)
    for root, dirnames, filenames in os.walk(source, topdown=False):
        if not dirnames and not filenames:
            Path(root).rmdir()


def _tail(output: str, limit: int = 400) -> str:
    text = " ".join(output.split())
    return text[-limit:]


def missing_for_requirements(
    requirements: Iterable[Any], *, settings: Settings | None = None
) -> list[str]:
    """Registry keys the given declared requirements need and this machine does not have."""
    from content_factory.models.weights import package_for_requirement

    settings = settings or get_settings()
    store = weight_store(settings)
    keys: list[str] = []
    for req in requirements:
        if getattr(req, "kind", "") == "skill":
            env = skill_env_by_key(getattr(req, "skill", ""))
            if env is not None and skill_env_status(env).state == "absent" and env.key not in keys:
                keys.append(env.key)
            continue
        package = package_for_requirement(req)
        if package is None:
            continue
        if package_status(package, store=store).state != "ready" and package.key not in keys:
            keys.append(package.key)
    return keys
