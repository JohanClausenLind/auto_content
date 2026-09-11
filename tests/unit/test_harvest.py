"""Bringing another machine's finished work home, without another machine.

Two GPU hosts produce for this project and only one is the control plane. Everything a second host
makes with the post chain, MMAudio, the TTS skills or Blender lands on *its* disk, because those
take absolute local paths and cannot be pointed at an endpoint the way HiDream can. So the finished
deliverable has to be collected — and the collection has to be provably safe against the ways the
far host actually fails here: it has gone to sleep mid-run, dropped off the tailnet mid-frame, and
it routinely sits a commit or two behind this checkout.

The far host is *described*, never had: `SUBPROCESS_RUN` is the one seam every remote call goes
through, so this suite needs no network, no ssh and no second machine.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from content_factory.config.settings import RemoteHost
from content_factory.schemas.base import file_sha256
from content_factory.services import harvest as harvest_mod

HOST = RemoteHost(name="nova", ssh="nova@example", repo_root="/home/nova/git/auto_content")


class FakeHost:
    """A far machine as a directory tree, plus the argv this code sent it.

    It answers only the two shapes `harvest.py` emits — the ssh probe and the rsync pull — and
    interprets the rsync by copying locally, honouring `--files-from`/`--from0` so that a test can
    assert that a file *not* in the manifest never crossed.
    """

    def __init__(self, root: Path, *, rev: str = "abc1234") -> None:
        self.root = root
        self.rev = rev
        self.active: list[dict] = []
        self.argv: list[list[str]] = []
        self.fail_rsync = False
        self.corrupt: set[str] = set()
        self.symlink: dict[str, Path] = {}

    # -- building the far host -------------------------------------------------------------
    def add_run(
        self,
        slug: str,
        *,
        files: dict[str, bytes],
        passed: bool = True,
        package: bool = True,
        qc_passed: bool | None = True,
        awaiting_review: bool = False,
        packages_raw: str | None = None,
    ) -> Path:
        ddir = self.root / "output" / slug / "deliverables" / "dlv_short0000001"
        ddir.mkdir(parents=True, exist_ok=True)
        entries = []
        for rel, blob in files.items():
            path = ddir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            entries.append(
                {
                    "role": "video" if rel.endswith(".mp4") else "image",
                    "path": rel,
                    "sha256": file_sha256(path),
                    "bytes": len(blob),
                    "content_type": "video/mp4" if rel.endswith(".mp4") else "image/png",
                }
            )
        (ddir / "run.json").write_text(json.dumps({"passed": passed, "stages": []}))
        (ddir / "qc").mkdir(exist_ok=True)
        (ddir / "qc" / "report.json").write_text(json.dumps({"passed": passed}))
        if awaiting_review:
            (ddir / "reviews" / "frames").mkdir(parents=True, exist_ok=True)
            (ddir / "reviews" / "frames" / "batch.json").write_text("{}")
        if package:
            manifest = ddir / "destination-packages" / "packages.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                packages_raw
                if packages_raw is not None
                else json.dumps(
                    [
                        {
                            "schema_version": 1,
                            "deliverable_id": "dlv_short0000001",
                            "destination": "export",
                            "visibility": "export_only",
                            "files": entries,
                            "qc_passed": qc_passed,
                            "built_at": "2026-09-10T12:00:00+00:00",
                        }
                    ]
                )
            )
        return ddir

    def hold(self, slug: str, *, pid: int = 4242, alive: bool = True) -> None:
        self.active.append(
            {
                "project_dir": str(self.root / "output" / slug),
                "pid": pid,
                "workflow": "image-set",
                "alive": alive,
            }
        )

    # -- answering ------------------------------------------------------------------------
    def run(self, argv, **kwargs):
        self.argv.append(list(argv))
        if argv[0] == "ssh":
            return self._probe()
        if argv[0] == "rsync":
            return self._rsync(argv)
        raise AssertionError(f"unexpected command: {argv[0]}")

    def _probe(self):
        runs = []
        base = self.root / "output"
        for project in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
            for ddir in sorted((project / "deliverables").glob("*")):
                report = ddir / "run.json"
                if not report.is_file():
                    continue
                manifest = ddir / "destination-packages" / "packages.json"
                reviews = ddir / "reviews" / "frames"
                runs.append(
                    {
                        "slug": project.name,
                        "deliverable_id": ddir.name,
                        "project_dir": str(project),
                        "deliverable_dir": str(ddir),
                        "passed": bool(json.loads(report.read_text())["passed"]),
                        "packages": manifest.read_text() if manifest.is_file() else "",
                        "awaiting_review": (reviews / "batch.json").is_file()
                        and not (reviews / "verdict.json").is_file(),
                    }
                )
        payload = {"root": str(base), "rev": self.rev, "runs": runs, "active": self.active}
        return subprocess.CompletedProcess(["ssh"], 0, json.dumps(payload), "")

    def _rsync(self, argv):
        if self.fail_rsync:
            return subprocess.CompletedProcess(
                argv, 30, "", "rsync: connection unexpectedly closed"
            )
        listing = next(a.split("=", 1)[1] for a in argv if a.startswith("--files-from="))
        sep = "\0" if "--from0" in argv else "\n"
        wanted = [w for w in Path(listing).read_text().split(sep) if w]
        source = Path(argv[-2].split(":", 1)[1])
        dest = Path(argv[-1])
        for rel in wanted:
            src = source / rel
            if not src.is_file():
                if "--ignore-missing-args" not in argv:
                    # What rsync really does, and what the first real harvest against nova hit:
                    # a name it cannot stat in the source is an error, exit 23, *after* copying
                    # everything else. A double that silently skipped it hid a live bug.
                    return subprocess.CompletedProcess(
                        argv, 23, "", f'rsync: [sender] link_stat "{src}" failed'
                    )
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if rel in self.symlink:
                out.symlink_to(self.symlink[rel])
                continue
            blob = src.read_bytes()
            # Same length as the original: a truncated file is caught by the cheap size check, and
            # the digest is what has to catch a file that is the right size and the wrong bytes.
            out.write_bytes(bytes(b ^ 0xFF for b in blob) if rel in self.corrupt else blob)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(harvest_mod, "SUBPROCESS_RUN", self.run)


@pytest.fixture
def far(tmp_path, monkeypatch) -> FakeHost:
    host = FakeHost(tmp_path / "nova")
    host.install(monkeypatch)
    return host


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "vegaserv"
    repo.mkdir(parents=True, exist_ok=True)
    return repo


FILM = {"exports/final.mp4": b"a film" * 40}
STILL = {"anchors/anchor.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64}


# -- what is ready, and what is only nearly ready ------------------------------------------
def test_a_finished_run_is_a_candidate(far) -> None:
    far.add_run("f01-sharpen", files=FILM)
    (run,) = harvest_mod.discover(HOST)
    assert run.state == "finished"
    assert [f.path for f in run.files] == ["exports/final.mp4"]


def test_a_run_at_the_human_review_gate_is_reported_not_dropped(far) -> None:
    """The resting state of an `image-set`: `review_frames` blocks before packaging, so there is
    no manifest and `passed` is false. Six of nova's seven overnight image sets ended here, and
    reporting nothing about them would read as "there is nothing to collect"."""
    far.add_run("a20-imageset-owl", files=STILL, passed=False, package=False, awaiting_review=True)
    (run,) = harvest_mod.discover(HOST)
    assert run.state == "awaiting-review"


def test_a_run_a_live_process_is_still_writing_is_left_alone(far) -> None:
    """The one case `passed: true` misses: a `--from` resume leaves the previous pass's flag on
    disk for the moment between the process starting and its first step writing the report."""
    far.add_run("k12-narrated-curve", files=FILM)
    far.hold("k12-narrated-curve")
    (run,) = harvest_mod.discover(HOST)
    assert run.state == "busy"


def test_a_registration_on_a_parent_directory_also_blocks(far) -> None:
    """Containment, not string equality."""
    far.add_run("nested", files=FILM)
    far.active.append(
        {"project_dir": str(far.root / "output"), "pid": 9, "workflow": "x", "alive": True}
    )
    assert harvest_mod.discover(HOST)[0].state == "busy"


def test_a_dead_registration_does_not_block(far) -> None:
    far.add_run("f02-penny", files=FILM)
    far.hold("f02-penny", alive=False)
    assert harvest_mod.discover(HOST)[0].state == "finished"


def test_version_skew_is_named_not_raised(far) -> None:
    """The far host is routinely a commit or two behind. `DeliveryPackage` forbids unknown fields,
    so a manifest from a different checkout must be reported as skew — and must not stop the other
    runs on that host coming home."""
    far.add_run("b01-narrated-sky", files=FILM)
    far.add_run("skewed", files=FILM, packages_raw=json.dumps([{"who": "knows"}]))
    states = {r.slug: r.state for r in harvest_mod.discover(HOST)}
    assert states == {"b01-narrated-sky": "finished", "skewed": "skew"}
    skewed = next(r for r in harvest_mod.discover(HOST) if r.slug == "skewed")
    assert skewed.rev == "abc1234"


def test_a_manifest_naming_a_path_outside_the_deliverable_is_refused_at_parse(far) -> None:
    """`DeliveryFile` already refuses an absolute path or a `..` segment, which is why the manifest
    is read through the contract and never as plain JSON."""
    for bad in ("../../etc/passwd", "/etc/passwd"):
        far.root.mkdir(parents=True, exist_ok=True)
        for existing in (far.root / "output").glob("*"):
            import shutil

            shutil.rmtree(existing)
        far.add_run(
            "evil",
            files=FILM,
            packages_raw=json.dumps(
                [
                    {
                        "schema_version": 1,
                        "deliverable_id": "dlv_short0000001",
                        "destination": "export",
                        "visibility": "export_only",
                        "files": [
                            {
                                "role": "video",
                                "path": bad,
                                "sha256": "0" * 64,
                                "bytes": 3,
                                "content_type": "video/mp4",
                            }
                        ],
                        "qc_passed": True,
                        "built_at": "2026-09-10T12:00:00+00:00",
                    }
                ]
            ),
        )
        assert harvest_mod.discover(HOST)[0].state == "skew"


# -- what actually crosses the wire ----------------------------------------------------------
def test_only_the_manifest_and_the_evidence_cross(far, tmp_path) -> None:
    """The whole design: a directory sync would bring the frames, the control passes and the chain
    steps, which is the thing this exists not to do."""
    ddir = far.add_run("j04-owl-set", files=FILM)
    (ddir / "sequence" / "frames").mkdir(parents=True)
    (ddir / "sequence" / "frames" / "0000.png").write_bytes(b"a decoy nobody asked for")
    (run,) = harvest_mod.discover(HOST)
    repo = _repo(tmp_path)
    harvest_mod.harvest_run(run, repo_root=repo)

    landed = harvest_mod.landing_dir("nova", "j04-owl-set", repo_root=repo)
    assert (landed / "exports" / "final.mp4").is_file()
    assert (landed / "qc" / "report.json").is_file()  # evidence, so a failure is diagnosable
    assert not (landed / "sequence").exists()


def test_the_file_list_is_nul_separated(far, tmp_path) -> None:
    """`DeliveryFile` permits a newline in a path and rsync's file list is newline-delimited
    otherwise. `--from0` closes the whole class."""
    far.add_run("f06-tern", files=FILM)
    (run,) = harvest_mod.discover(HOST)
    harvest_mod.harvest_run(run, repo_root=_repo(tmp_path))
    (rsync,) = [a for a in far.argv if a[0] == "rsync"]
    assert "--from0" in rsync
    assert "--safe-links" in rsync
    assert "-L" not in rsync
    assert any(a.startswith("--timeout=") for a in rsync)
    # Evidence is requested optimistically and rsync errors (exit 23) on a name it cannot stat.
    assert "--ignore-missing-args" in rsync


# -- refusing to believe a partial transfer ---------------------------------------------------
def test_a_file_of_the_right_size_and_the_wrong_bytes_refuses_the_whole_run(far, tmp_path) -> None:
    far.add_run("f07-kanna", files=FILM)
    far.corrupt.add("exports/final.mp4")
    (run,) = harvest_mod.discover(HOST)
    repo = _repo(tmp_path)
    with pytest.raises(harvest_mod.HarvestError, match="arrived corrupt"):
        harvest_mod.harvest_run(run, repo_root=repo)
    assert not harvest_mod.landing_dir("nova", "f07-kanna", repo_root=repo).exists()
    assert not (repo / "videos").exists()


def test_a_symlink_is_refused_before_it_is_hashed(far, tmp_path) -> None:
    """Hashing follows the link, so a symlink pointing at a local file of the right content would
    verify perfectly and be filed as harvested."""
    decoy = tmp_path / "decoy.mp4"
    decoy.write_bytes(b"a film" * 40)
    far.add_run("f08-curve", files=FILM)
    far.symlink["exports/final.mp4"] = decoy
    (run,) = harvest_mod.discover(HOST)
    with pytest.raises(harvest_mod.HarvestError, match="symlink"):
        harvest_mod.harvest_run(run, repo_root=_repo(tmp_path))


def test_a_failed_transfer_leaves_nothing_behind_and_retries(far, tmp_path) -> None:
    """nova has dropped off the tailnet mid-run twice. An interrupted pull must cost a retry and
    never a deliverable that looks finished."""
    far.add_run("f03-bellrock", files=FILM)
    (run,) = harvest_mod.discover(HOST)
    repo = _repo(tmp_path)
    far.fail_rsync = True
    with pytest.raises(harvest_mod.HarvestError):
        harvest_mod.harvest_run(run, repo_root=repo)
    assert not harvest_mod.landing_dir("nova", "f03-bellrock", repo_root=repo).exists()
    assert not list((harvest_mod.harvest_root(repo) / "nova" / ".incoming").glob("*"))

    far.fail_rsync = False
    assert harvest_mod.harvest_run(run, repo_root=repo).result == "harvested"


# -- doing it twice ---------------------------------------------------------------------------
def test_a_second_pass_transfers_nothing(far, tmp_path) -> None:
    far.add_run("f04-vitrail", files=FILM)
    (run,) = harvest_mod.discover(HOST)
    repo = _repo(tmp_path)
    harvest_mod.harvest_run(run, repo_root=repo)
    before = len([a for a in far.argv if a[0] == "rsync"])
    assert harvest_mod.harvest_run(run, repo_root=repo).result == "already here"
    assert len([a for a in far.argv if a[0] == "rsync"]) == before


def test_bytes_without_a_sidecar_are_a_failed_harvest_and_get_redone(far, tmp_path) -> None:
    far.add_run("f05-salinas", files=FILM)
    (run,) = harvest_mod.discover(HOST)
    repo = _repo(tmp_path)
    harvest_mod.harvest_run(run, repo_root=repo)
    (harvest_mod.landing_dir("nova", "f05-salinas", repo_root=repo) / harvest_mod.SIDECAR).unlink()
    assert harvest_mod.harvest_run(run, repo_root=repo).result == "harvested"


def test_the_same_slug_with_different_bytes_is_a_new_deliverable(far, tmp_path) -> None:
    """`run-local` writes every run of a lane to the same project directory unless told otherwise,
    so a name cannot say whether something is new. The digest can."""
    far.add_run("image-set", files=FILM)
    repo = _repo(tmp_path)
    harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    far.add_run("image-set", files={"exports/final.mp4": b"a different film" * 20})
    assert harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo).result == (
        "harvested"
    )


# -- the gallery ------------------------------------------------------------------------------
def test_the_film_lands_in_the_gallery(far, tmp_path) -> None:
    far.add_run("f01-sharpen", files=FILM)
    repo = _repo(tmp_path)
    outcome = harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    assert outcome.gallery == "videos/f01-sharpen.mp4"
    assert (repo / "videos" / "f01-sharpen.mp4").read_bytes() == b"a film" * 40


def test_a_stills_lane_publishes_nothing(far, tmp_path) -> None:
    far.add_run("a02-single-image-forge", files=STILL)
    repo = _repo(tmp_path)
    outcome = harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    assert outcome.gallery is None
    assert not (repo / "videos").exists()


def test_a_film_that_failed_qc_comes_home_but_is_not_published(far, tmp_path) -> None:
    """`DeliveryPackage.qc_passed` is explicitly allowed to be false. The bytes are evidence; the
    gallery is for finished films."""
    far.add_run("f09-bad", files=FILM, qc_passed=False)
    repo = _repo(tmp_path)
    outcome = harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    assert outcome.result == "harvested"
    assert outcome.gallery is None
    assert (
        harvest_mod.landing_dir("nova", "f09-bad", repo_root=repo) / "exports/final.mp4"
    ).exists()


def test_a_gallery_name_already_taken_by_other_bytes_is_reported(far, tmp_path) -> None:
    """Two runs called the same thing on two machines is a real collision, and the operator should
    be told rather than have one of the films disappear."""
    repo = _repo(tmp_path)
    (repo / "videos").mkdir(parents=True)
    # Deliberately the SAME LENGTH as the harvested film: two different films can be the same
    # number of bytes, so identity here has to be the digest and not the size.
    (repo / "videos" / "clash.mp4").write_bytes(b"other!" * 40)
    far.add_run("clash", files=FILM)
    outcome = harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    assert outcome.result == "harvested"  # the bytes are home
    assert outcome.gallery is None
    assert "already in the gallery" in outcome.detail


# -- a whole pass ------------------------------------------------------------------------------
def test_one_unreachable_host_does_not_stop_the_others(monkeypatch, tmp_path) -> None:
    """nova has been asleep once and off the network once. A pass that gave up on the first
    unreachable host would collect nothing from the ones that are up."""
    good = FakeHost(tmp_path / "good")
    good.add_run("f01-sharpen", files=FILM)

    def run(argv, **kwargs):
        if "down@example" in argv:
            return subprocess.CompletedProcess(argv, 255, "", "ssh: connect: No route to host")
        return good.run(argv, **kwargs)

    monkeypatch.setattr(harvest_mod, "SUBPROCESS_RUN", run)
    down = RemoteHost(name="down", ssh="down@example")
    outcomes = harvest_mod.harvest([down, HOST], repo_root=_repo(tmp_path))
    assert [o.result for o in outcomes] == ["unreachable", "harvested"]


def test_dry_run_transfers_nothing(far, tmp_path) -> None:
    far.add_run("f01-sharpen", files=FILM)
    repo = _repo(tmp_path)
    outcomes = harvest_mod.harvest([HOST], dry_run=True, repo_root=repo)
    assert [o.result for o in outcomes] == ["would harvest"]
    assert not [a for a in far.argv if a[0] == "rsync"]
    assert not harvest_mod.harvest_root(repo).exists()


def test_a_truncated_file_is_caught_before_it_is_hashed(far, tmp_path, monkeypatch) -> None:
    """The size check is a cheap pre-filter: sha256 subsumes it, but not before reading the file."""
    far.add_run("truncated", files=FILM)
    original = far._rsync

    def truncating(argv):
        done = original(argv)
        (Path(argv[-1]) / "exports" / "final.mp4").write_bytes(b"half")
        return done

    far._rsync = truncating
    (run,) = harvest_mod.discover(HOST)
    with pytest.raises(harvest_mod.HarvestError, match="manifest says"):
        harvest_mod.harvest_run(run, repo_root=_repo(tmp_path))


# -- configuration ------------------------------------------------------------------------------
def test_no_hosts_configured_is_the_default_and_harvests_nothing() -> None:
    from content_factory.config import get_settings

    assert get_settings().remote.enabled() is False
    assert harvest_mod.harvest([]) == []


def test_hosts_come_from_single_quoted_json_in_the_env(monkeypatch) -> None:
    """`.env` is sourced by bash, which eats inner double quotes; the resulting pydantic error does
    not mention quoting, so the shape that works is worth pinning."""
    from content_factory.config.settings import Settings

    monkeypatch.setenv(
        "CF__REMOTE__HOSTS",
        '[{"name":"nova","ssh":"nova@100.82.150.94","runs_root":"output/runs"}]',
    )
    remote = Settings().remote
    assert remote.enabled()
    nova = remote.host_named("nova")
    assert nova is not None
    assert nova.runs_root == "output/runs"
    assert remote.host_named("absent") is None


def test_an_unknown_field_on_a_host_is_an_error(monkeypatch) -> None:
    from content_factory.config.settings import Settings

    monkeypatch.setenv("CF__REMOTE__HOSTS", '[{"name":"nova","ssh":"n","typo_root":"/x"}]')
    with pytest.raises(ValueError, match="typo_root"):
        Settings()


def test_the_same_film_already_in_the_gallery_is_recognised_not_refused(far, tmp_path) -> None:
    """Harvesting a film this machine already has a copy of is not a collision. Only different
    bytes under the same name are."""
    repo = _repo(tmp_path)
    (repo / "videos").mkdir(parents=True)
    (repo / "videos" / "twice.mp4").write_bytes(b"a film" * 40)
    far.add_run("twice", files=FILM)
    outcome = harvest_mod.harvest_run(harvest_mod.discover(HOST)[0], repo_root=repo)
    assert outcome.gallery == "videos/twice.mp4"
    assert outcome.detail == ""
