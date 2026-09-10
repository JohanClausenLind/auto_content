"""Collect finished deliverables from the other machines that generate for this repo.

Two GPU hosts produce for this project and only one of them is the control plane. A second host
can serve *drawing* over HTTP — HiDream takes base64 in and hands base64 back, so nothing it makes
touches its own disk — but the stages that decide how a film ends (the post chain, MMAudio, the TTS
skills, Blender, ffmpeg) all take absolute local paths and cannot be pointed at an endpoint. The
moment a second host runs a whole lane, the finished work is on the wrong disk.

**Only the deliverable comes home.** A run writes thousands of control-pass frames, per-frame
markers and chain steps; the thing worth moving is the film, plus a few hundred KB of evidence that
makes it diagnosable. What to move is not guessed: ``compile_destination_packages`` already writes
``destination-packages/packages.json`` naming every shippable file with its role, its
deliverable-relative path and its sha256, so this reads a manifest a stage produced rather than
inventing a second idea of what a lane delivers.

The manifest is parsed through :class:`DeliveryPackage` rather than as plain JSON, and that is what
makes it safe to read from another machine: ``DeliveryFile`` already refuses an absolute path or a
``..`` segment, and ``extra="forbid"`` means a host on a different commit is reported as skew
rather than half-transferred. The threat model is a **skewed or buggy** worker, not a hostile one —
which is why cheap validation is worth it and a sandbox is not.

The direction is always **pull**: the control plane reaches the worker. A worker never needs, and
is never given, write access to this machine's run directories.

Idempotence has no ledger behind it. A harvested run carries a ``harvest.json`` sidecar written
*last*, so "already here" is derivable from the bytes on disk, a half-finished harvest is visibly
half-finished, and ``rm -rf output/harvest/<host>/<slug>`` is a complete and obvious undo.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import ValidationError

from content_factory.config import get_settings
from content_factory.config.settings import RemoteHost
from content_factory.deliverables.gallery import publish_film
from content_factory.schemas.base import file_sha256
from content_factory.schemas.delivery import DeliveryFile, DeliveryPackage
from content_factory.services.local import REPO_ROOT

SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
"""The single seam every remote call goes through. It is what lets the tests describe a far host
instead of having one, so ``just test`` needs no network, no keys and no second machine — and it is
also why ruff's S603/S607 never fire here, the same arrangement ``services/local.py`` and
``postchain/runner.py`` use."""

SIDECAR = "harvest.json"
"""Written last, after every digest has been checked. Its presence is the only claim this code
makes that a harvest finished."""


class HarvestError(RuntimeError):
    """A host could not be asked, or answered something this code will not act on."""


# Read-only probe, sent to the far host over stdin as
# `python3 - <repo_root> <runs_root> <services_dir>`.
# Plain stdlib and no import of this repo, deliberately: a worker is routinely a commit or two
# behind the control plane, and a probe that needed the repo's own code would fail exactly when the
# drift it exists to report is largest.
_PROBE = r"""
import json, os, subprocess, sys
repo = os.path.expanduser(sys.argv[1])
runs = os.path.expanduser(sys.argv[2])
services = os.path.expanduser(sys.argv[3])
root = runs if os.path.isabs(runs) else os.path.join(repo, runs)
services = services if os.path.isabs(services) else os.path.join(repo, services)

def alive(pid):
    # Decided HERE. The control plane's process table knows nothing about this machine's pids, so
    # testing them there would be a coin flip on whether a live run looks finished.
    try:
        return os.path.isdir("/proc/%d" % int(pid))
    except (ValueError, TypeError):
        return False

try:
    rev = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
except Exception:
    rev = ""

active = []
try:
    names = sorted(os.listdir(os.path.join(services, "runs")))
except OSError:
    names = []
for name in names:
    if not name.endswith(".json"):
        continue
    try:
        with open(os.path.join(services, "runs", name)) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        continue
    project = reg.get("project_dir") or ""
    active.append({"project_dir": os.path.realpath(project) if project else "",
                   "pid": reg.get("pid"), "workflow": reg.get("workflow") or "",
                   "alive": alive(reg.get("pid"))})

found = []
try:
    slugs = sorted(os.listdir(root))
except OSError:
    slugs = []
for slug in slugs:
    project = os.path.join(root, slug)
    try:
        ids = sorted(os.listdir(os.path.join(project, "deliverables")))
    except OSError:
        continue
    for did in ids:
        ddir = os.path.join(project, "deliverables", did)
        report = os.path.join(ddir, "run.json")
        if not os.path.isfile(report):
            continue
        try:
            with open(report) as fh:
                passed = bool(json.load(fh).get("passed"))
        except (OSError, ValueError):
            continue
        manifest = os.path.join(ddir, "destination-packages", "packages.json")
        packages = ""
        if os.path.isfile(manifest):
            try:
                with open(manifest) as fh:
                    packages = fh.read()
            except OSError:
                packages = ""
        found.append({
            "slug": slug, "deliverable_id": did,
            "project_dir": os.path.realpath(project), "deliverable_dir": os.path.realpath(ddir),
            "passed": passed, "packages": packages,
            "awaiting_review": (
                os.path.isfile(os.path.join(ddir, "reviews", "frames", "batch.json"))
                and not os.path.isfile(os.path.join(ddir, "reviews", "frames", "verdict.json"))),
        })

json.dump({"root": root, "rev": rev, "runs": found, "active": active}, sys.stdout)
"""

State = Literal["finished", "awaiting-review", "unfinished", "busy", "skew"]


@dataclass(frozen=True)
class RemoteRun:
    """One run on another machine, and what this host is able to do about it."""

    host: RemoteHost
    slug: str
    """The run's project directory name — what the operator called the run."""
    deliverable_id: str
    deliverable_dir: str
    """Absolute, on the far host. Only ever used as an rsync source."""
    project_dir: str
    passed: bool
    packages: tuple[DeliveryPackage, ...]
    state: State
    detail: str = ""
    rev: str = ""
    """The far host's short git rev, so skew can be reported as skew instead of as corruption."""

    @property
    def files(self) -> tuple[DeliveryFile, ...]:
        """Every distinct file the packages name, in manifest order.

        One package per destination, and destinations overwhelmingly ship the same bytes, so the
        union is taken rather than the first package's list. A path named twice with two digests is
        a manifest contradicting itself, and is refused before anything is transferred.
        """
        seen: dict[str, DeliveryFile] = {}
        for package in self.packages:
            for entry in package.files:
                first = seen.get(entry.path)
                if first is None:
                    seen[entry.path] = entry
                elif first.sha256 != entry.sha256:
                    msg = (
                        f"{self.slug}: manifest names {entry.path} twice with different digests"
                        f" ({first.sha256[:12]} and {entry.sha256[:12]})"
                    )
                    raise HarvestError(msg)
        return tuple(seen.values())

    @property
    def digest(self) -> str:
        """Identity of *these bytes*, not of this run name.

        A slug is reused — ``run-local`` writes every run of a lane to the same project directory
        unless told otherwise — so the name cannot say whether something is new. Built from the
        files rather than the manifest text so that repacking, which rewrites ``built_at``, does not
        look like a new deliverable.
        """
        material = sorted((f.role, f.path, f.sha256) for f in self.files)
        return hashlib.sha256(json.dumps(material).encode()).hexdigest()

    @property
    def qc_passed(self) -> bool | None:
        for package in self.packages:
            if package.qc_passed is not None:
                return package.qc_passed
        return None


@dataclass(frozen=True)
class Outcome:
    """One line of what happened, for the operator and for the tests."""

    host: str
    slug: str
    result: str
    detail: str = ""
    files: int = 0
    bytes: int = 0
    gallery: str | None = None

    def __str__(self) -> str:
        size = f"{self.bytes / 1e6:.1f} MB" if self.bytes else ""
        tail = self.gallery or self.detail
        return f"{self.host:<9} {self.slug:<26} {self.result:<16} {size:>9}  {tail}".rstrip()


def harvest_root(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / get_settings().remote.harvest_root


def landing_dir(host: str, slug: str, *, repo_root: Path = REPO_ROOT) -> Path:
    return harvest_root(repo_root) / host / slug


def _run(argv: Sequence[str], *, timeout: int, stdin: str | None = None) -> str:
    try:
        done = SUBPROCESS_RUN(
            list(argv), input=stdin, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"{argv[0]} timed out after {timeout}s"
        raise HarvestError(msg) from exc
    except OSError as exc:
        msg = f"{argv[0]} could not be run: {exc}"
        raise HarvestError(msg) from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().replace("\n", " ")[:300]
        msg = f"{argv[0]} failed ({done.returncode}): {detail}"
        raise HarvestError(msg)
    return done.stdout or ""


def _held_by(project_dir: str, active: Sequence[dict]) -> str:
    """Whether a live run on the far host is writing into this project directory.

    Containment, not string equality: a registration on a parent directory is also a reason to
    leave a run alone, and a resolved path is what the probe returns for exactly that comparison.
    """
    target = PurePosixPath(project_dir)
    for entry in active:
        if not entry.get("alive") or not entry.get("project_dir"):
            continue
        held = PurePosixPath(entry["project_dir"])
        if held == target or held in target.parents or target in held.parents:
            return f"{entry.get('workflow') or 'a run'} (pid {entry.get('pid')})"
    return ""


def discover(host: RemoteHost) -> list[RemoteRun]:
    """Every run on one host and what state it is in, in one round trip.

    Three things have to be true before a deliverable is collectable, and each rules out a different
    way of picking something up half-made:

    * ``run.json`` says ``passed``. The report is rewritten after *every* step with ``passed``
      false, so a run still executing excludes itself without being asked.
    * ``destination-packages/packages.json`` exists, so there is a manifest to verify against.
    * no live registration names the project directory. This is the one case the ``passed`` flag
      misses: a ``--from`` resume leaves the previous pass's ``passed: true`` on disk for the moment
      between the process starting and its first step writing the report.

    Everything else is still reported, because a run stopped at the human review gate is the normal
    resting state of an ``image-set`` and silence about it would read as "nothing to collect".
    """
    settings = get_settings().remote
    raw = _run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={min(settings.ssh_timeout_s, 10)}",
            host.ssh,
            "python3",
            "-",
            host.repo_root,
            host.runs_root,
            host.services_dir,
        ],
        timeout=settings.ssh_timeout_s,
        stdin=_PROBE,
    )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        msg = f"{host.name}: probe returned no JSON: {raw.strip()[:200]}"
        raise HarvestError(msg) from exc

    rev = payload.get("rev") or ""
    active = payload.get("active") or []
    runs: list[RemoteRun] = []
    for found in payload.get("runs", []):
        packages: tuple[DeliveryPackage, ...] = ()
        state: State = "unfinished"
        detail = ""
        blob = found.get("packages") or ""
        if blob:
            try:
                packages = tuple(DeliveryPackage.model_validate(i) for i in json.loads(blob))
            except (ValidationError, ValueError, TypeError) as exc:
                # Not an exception for the whole pass: the far host being on a different commit is
                # the expected reason, and one skewed run must not stop the others coming home.
                state, detail = "skew", f"packages.json does not match this checkout ({exc})"[:200]
        if packages:
            state = "finished" if found.get("passed") else "unfinished"
        elif state != "skew":
            state = "awaiting-review" if found.get("awaiting_review") else "unfinished"
        if state == "finished":
            held = _held_by(found["project_dir"], active)
            if held:
                state, detail = "busy", f"held by {held}"
        runs.append(
            RemoteRun(
                host=host,
                slug=found["slug"],
                deliverable_id=found["deliverable_id"],
                deliverable_dir=found["deliverable_dir"],
                project_dir=found["project_dir"],
                passed=bool(found.get("passed")),
                packages=packages,
                state=state,
                detail=detail,
                rev=rev,
            )
        )
    return runs


def already_harvested(run: RemoteRun, *, repo_root: Path = REPO_ROOT) -> bool:
    """Whether these exact bytes are already on this machine.

    Derived from the sidecar rather than from a ledger: no second source of truth to fall out of
    step, and a landing directory with files but no sidecar is a harvest that failed part way and
    gets redone rather than trusted.
    """
    sidecar = landing_dir(run.host.name, run.slug, repo_root=repo_root) / SIDECAR
    try:
        return json.loads(sidecar.read_text()).get("digest") == run.digest
    except (OSError, ValueError):
        return False


def fetch_argv(run: RemoteRun, *, list_file: Path, dest: Path, partial: Path) -> list[str]:
    """The exact rsync invocation, separated out because it is the part worth asserting on.

    ``--files-from`` is the whole design: a directory sync would bring the frames, the control
    passes and the chain steps, which is the thing this exists not to do.

    ``--from0`` because ``DeliveryFile`` permits a newline in a path and rsync's file list is
    newline-delimited otherwise — one flag closes the whole class. ``--safe-links`` and no ``-L``
    because a symlink that arrived as a symlink would be hashed *through*, reading a local file and
    calling it harvested; :func:`verify` refuses one outright, and this stops it crossing at all.
    """
    settings = get_settings().remote
    argv = [
        "rsync",
        "-a",
        "--safe-links",
        "--from0",
        f"--files-from={list_file}",
        "--partial-dir",
        str(partial),
        f"--timeout={settings.transfer_timeout_s}",
    ]
    if settings.bwlimit_kbps:
        argv.append(f"--bwlimit={settings.bwlimit_kbps}")
    argv += [f"{run.host.ssh}:{run.deliverable_dir}/", f"{dest}/"]
    return argv


def fetch(run: RemoteRun, dest: Path) -> None:
    """Pull the manifest's files, plus whatever evidence exists, into a staging directory."""
    settings = get_settings().remote
    dest.mkdir(parents=True, exist_ok=True)
    wanted = [f.path for f in run.files]
    wanted += [e for e in settings.evidence if e not in {f.path for f in run.files}]
    wanted.append("destination-packages/packages.json")
    handle = tempfile.NamedTemporaryFile("w", suffix=".files", delete=False, newline="")
    try:
        # NUL-separated, matching --from0. Evidence files that do not exist are simply not sent;
        # rsync does not fail a transfer over a name it cannot find in the source.
        handle.write("\0".join(dict.fromkeys(wanted)) + "\0")
        handle.close()
        _run(
            fetch_argv(
                run, list_file=Path(handle.name), dest=dest, partial=dest / ".rsync-partial"
            ),
            timeout=settings.transfer_timeout_s + 60,
        )
    finally:
        os.unlink(handle.name)


def verify(run: RemoteRun, staged: Path) -> int:
    """Re-hash every arrived file against the manifest; return the total bytes.

    A partial transfer is the expected failure — the second host has gone off the network mid-run
    twice — so nothing is published or recorded until every digest matches. An interrupted pull
    therefore costs a retry and never a deliverable that looks finished and is not.
    """
    total = 0
    for entry in run.files:
        path = staged / entry.path
        if path.is_symlink():
            # Checked before the hash, not after: hashing follows the link, so a symlink pointing
            # at a local file of the right content would otherwise verify perfectly.
            msg = f"{run.slug}: {entry.path} arrived as a symlink"
            raise HarvestError(msg)
        if not path.is_file():
            msg = f"{run.slug}: {entry.path} did not arrive"
            raise HarvestError(msg)
        size = path.stat().st_size
        if size != entry.bytes:
            msg = f"{run.slug}: {entry.path} is {size} bytes, manifest says {entry.bytes}"
            raise HarvestError(msg)
        actual = file_sha256(path)
        if actual != entry.sha256:
            msg = (
                f"{run.slug}: {entry.path} arrived corrupt"
                f" (wanted {entry.sha256[:12]}, got {actual[:12]})"
            )
            raise HarvestError(msg)
        total += size
    return total


def commit(run: RemoteRun, staged: Path, total: int, *, repo_root: Path = REPO_ROOT) -> Path:
    """Move a verified harvest into place, then write the sidecar that says so.

    A directory rename is atomic on one filesystem, so nothing half-transferred is ever visible at
    the landing path. The sidecar goes last for the same reason: it is the completion marker, and a
    landing directory carrying bytes without one is a failure the next pass redoes.
    """
    landing = landing_dir(run.host.name, run.slug, repo_root=repo_root)
    if landing.exists():
        shutil.rmtree(landing)
    landing.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staged / ".rsync-partial", ignore_errors=True)
    os.replace(staged, landing)
    (landing / SIDECAR).write_text(
        json.dumps(
            {
                "host": run.host.name,
                "origin": run.project_dir,
                "origin_rev": run.rev,
                "slug": run.slug,
                "deliverable_id": run.deliverable_id,
                "digest": run.digest,
                "files": len(run.files),
                "bytes": total,
                "qc_passed": run.qc_passed,
                "harvested_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
            sort_keys=True,
        )
    )
    return landing


def publish(run: RemoteRun, landing: Path, *, repo_root: Path = REPO_ROOT) -> str | None:
    """Put the film where finished films already live, under the run's own name.

    Deliberately *not* prefixed with the host. ``videos/`` is a flat directory of run names and
    prefixing would sort one machine's work away from the other's; a name already taken by
    different bytes is a genuine collision — two runs called the same thing — and is reported
    rather than silently resolved either way.

    A package whose QC failed is brought home and not published: it is evidence, and the gallery is
    for finished films.
    """
    from content_factory.workflows.stages import primary_film

    settings = get_settings().remote
    if not settings.publish_to_gallery or run.qc_passed is False:
        return None
    film = primary_film(run.files)
    if film is None:
        return None  # a stills lane cut nothing; the same silence a local single-image run keeps
    return publish_film(
        name=run.slug,
        source=landing / film.path,
        gallery_dir=get_settings().gallery.dir,
        repo_root=repo_root,
        replace=False,
    )


def harvest_run(run: RemoteRun, *, dry_run: bool = False, repo_root: Path = REPO_ROOT) -> Outcome:
    """Fetch, verify, land and publish one deliverable. Safe to call twice."""
    if already_harvested(run, repo_root=repo_root):
        return Outcome(run.host.name, run.slug, "already here")
    files = run.files  # raises on a self-contradicting manifest, before anything is transferred
    if dry_run:
        return Outcome(
            run.host.name,
            run.slug,
            "would harvest",
            files=len(files),
            bytes=sum(f.bytes for f in files),
        )
    staged = harvest_root(repo_root) / run.host.name / ".incoming" / f"{run.slug}.{os.getpid()}"
    shutil.rmtree(staged, ignore_errors=True)
    try:
        fetch(run, staged)
        total = verify(run, staged)
        landing = commit(run, staged, total, repo_root=repo_root)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    try:
        gallery = publish(run, landing, repo_root=repo_root)
    except FileExistsError as exc:
        return Outcome(
            run.host.name, run.slug, "harvested", str(exc), len(files), total, gallery=None
        )
    return Outcome(run.host.name, run.slug, "harvested", "", len(files), total, gallery=gallery)


_LEFT: dict[State, str] = {
    "awaiting-review": "awaiting review",
    "unfinished": "not finished",
    "busy": "still running",
    "skew": "version skew",
}


def harvest(
    hosts: Sequence[RemoteHost] | None = None,
    *,
    dry_run: bool = False,
    repo_root: Path = REPO_ROOT,
) -> list[Outcome]:
    """Collect from every configured host. One host failing never stops the others.

    A host that cannot be reached is reported and skipped, because the common reason for it is the
    one this project has met twice — the far machine asleep or off the tailnet — and a pass that
    aborted on the first unreachable host would collect nothing from the ones that are up. Same
    discipline as ``services/gpu_pool``, and for the same reason.
    """
    hosts = list(hosts if hosts is not None else get_settings().remote.hosts)
    outcomes: list[Outcome] = []
    for host in hosts:
        try:
            runs = discover(host)
        except HarvestError as exc:
            outcomes.append(Outcome(host.name, "-", "unreachable", str(exc)[:100]))
            continue
        for run in runs:
            if run.state != "finished":
                outcomes.append(
                    Outcome(host.name, run.slug, _LEFT[run.state], run.detail or "left there")
                )
                continue
            try:
                outcomes.append(harvest_run(run, dry_run=dry_run, repo_root=repo_root))
            except HarvestError as exc:
                # Nothing landed and nothing published, so the next pass retries this run.
                outcomes.append(Outcome(host.name, run.slug, "refused", str(exc)[:100]))
    return outcomes
