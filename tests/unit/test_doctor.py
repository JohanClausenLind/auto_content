from __future__ import annotations

from content_factory.doctor import Status, run_doctor


def test_doctor_reports_every_check_with_plain_language_fix() -> None:
    report = run_doctor()
    names = {c.name for c in report.checks}
    assert {"config", "docker", "ffmpeg", "node", "uv", "gpu", "disk"} <= names
    for c in report.checks:
        if c.status in {Status.fail, Status.warn}:
            assert c.fix, f"{c.name} has no remediation text"
    assert report.model_dump(mode="json")["checks"]


def test_the_pcie_counters_that_saw_the_card_leave_the_bus_are_a_check(tmp_path, monkeypatch):
    """A GPU that falls off the PCIe bus takes the desktop with it and looks like a frozen PC.

    Measured on vegaserv 2026-09-10 03:12: `AER: Uncorrectable ... TLP UnsupReq` on the card's
    root port, `Xid 79, GPU has fallen off the bus`, then Xorg spinning forever inside the dead
    driver. Nothing in software prevents that, so what `doctor` owes the operator is the early
    warning — the root port's AER counters, which are readable without root and which no other
    check looks at.
    """
    from content_factory import doctor

    port = tmp_path / "0000:00:01.0"
    port.mkdir()

    def counters(cor: int, nonfatal: int, fatal: int) -> None:
        (port / "aer_rootport_total_err_cor").write_text(f"{cor}\n")
        (port / "aer_rootport_total_err_nonfatal").write_text(f"{nonfatal}\n")
        (port / "aer_rootport_total_err_fatal").write_text(f"{fatal}\n")

    monkeypatch.setattr(doctor, "_gpu_root_port", lambda: port)
    monkeypatch.setattr(doctor, "_run", lambda *_a, **_k: (1, ""))  # no nvidia-smi in the suite

    def health(cor: int, nonfatal: int, fatal: int) -> doctor.Check:
        counters(cor, nonfatal, fatal)
        ctx = doctor._Ctx()
        doctor._gpu_dropout_checks(ctx)
        return next(c for c in ctx.checks if c.name == "gpu_pcie_health")

    assert health(0, 0, 0).status is doctor.Status.ok
    # The night's own numbers: one uncorrectable non-fatal error was the whole event.
    faulted = health(0, 1, 0)
    assert faulted.status is doctor.Status.fail
    assert faulted.fix and "Xid 79" in faulted.fix
    assert health(500, 0, 0).status is doctor.Status.warn  # a link on its way out
    assert health(3, 0, 0).status is doctor.Status.ok  # a few retries are not news


def test_both_dropout_mitigations_are_reported_as_unapplied_until_they_are(tmp_path, monkeypatch):
    """ASPM off and a power cap under the stock ceiling. Neither is something a repo can set —
    both need root — so the job here is to keep saying so until somebody has."""
    from content_factory import doctor

    port = tmp_path / "0000:00:01.0"
    port.mkdir()
    for name in ("cor", "nonfatal", "fatal"):
        (port / f"aer_rootport_total_err_{name}").write_text("0\n")
    monkeypatch.setattr(doctor, "_gpu_root_port", lambda: port)

    def checks(power_csv: str) -> dict[str, doctor.Check]:
        monkeypatch.setattr(doctor, "_run", lambda *_a, **_k: (0, power_csv))
        ctx = doctor._Ctx()
        doctor._gpu_dropout_checks(ctx)
        return {c.name: c for c in ctx.checks}

    stock = checks("350.00, 350.00")["gpu_power_cap"]
    assert stock.status is doctor.Status.warn
    assert stock.fix and "nvidia-smi -pl 301" in stock.fix  # 86 % of the ceiling
    assert checks("300.00, 350.00")["gpu_power_cap"].status is doctor.Status.ok

    aspm = checks("300.00, 350.00")["gpu_aspm"]
    assert aspm.status in {doctor.Status.ok, doctor.Status.warn}
    if aspm.status is doctor.Status.warn:
        assert aspm.fix and "pcie_aspm=off" in aspm.fix
