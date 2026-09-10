"""The log that should have existed on the night the card left the bus.

2026-09-10 03:12: `AER: Uncorrectable ... TLP UnsupReq`, `Xid 79`, and no record anywhere of what
the GPU had been doing. Every published account of this failure was solved by a different cause,
so the only thing that shortens the next one is having sampled the machine. These tests hold the
sampler to being pure, total and honest about missing data — a sampler that raises on an odd line
is a sampler that is not running when it matters.
"""

from __future__ import annotations

from pathlib import Path

from content_factory.services import gpu_telemetry as tel

SMI_LINE = (
    "42, 318.44, 1770, 9751, 99, 21044, P2, 3, 0x0000000000000004, Not Active, Active, Not Active"
)
CLEAN_AER = {
    "aer_rootport_total_err_cor": "0",
    "aer_rootport_total_err_nonfatal": "0",
    "aer_rootport_total_err_fatal": "0",
}


def test_a_sample_carries_the_columns_that_separate_the_explanations() -> None:
    s = tel.parse_sample("2026-09-10T03:12:22+0200", SMI_LINE, CLEAN_AER)
    # Temperature and power say "hot" or "sagging"; the throttle flags say which the driver thinks.
    assert (s.temperature_c, s.power_w) == ("42", "318.44")
    assert (s.sw_power_cap, s.hw_thermal) == ("Active", "Not Active")
    # Clocks, because one published fix for this exact failure was locking them.
    assert (s.sm_mhz, s.mem_mhz, s.pstate) == ("1770", "9751", "P2")
    assert s.pcie_gen == "3"
    assert not s.faulted


def test_one_non_fatal_pcie_error_is_the_whole_event() -> None:
    """The night's own numbers: a single uncorrectable non-fatal error and the card was gone.
    Correctable errors are retried in hardware and are invisible everywhere else, so they are
    recorded but do not by themselves mean the link has failed."""
    faulted = tel.parse_sample("t", SMI_LINE, {**CLEAN_AER, "aer_rootport_total_err_nonfatal": "1"})
    assert faulted.faulted
    fatal = tel.parse_sample("t", SMI_LINE, {**CLEAN_AER, "aer_rootport_total_err_fatal": "2"})
    assert fatal.faulted
    retried = tel.parse_sample("t", SMI_LINE, {**CLEAN_AER, "aer_rootport_total_err_cor": "900"})
    assert not retried.faulted


def test_a_short_or_unreadable_line_still_produces_a_row() -> None:
    short = tel.parse_sample("t", "51, 210.0", {})
    assert (short.temperature_c, short.power_w) == ("51", "210.0")
    assert short.mem_mhz == "" and short.aer_cor == ""
    assert short.csv_row().count(",") == len(tel.COLUMNS) - 1
    assert not short.faulted  # missing counters are not a fault claim


def test_a_comma_inside_a_field_cannot_shift_every_later_column() -> None:
    odd = tel.parse_sample("t", "42, 318.44, 1770, 9751, 99, 21044, P2, 3, a,b, x, y, z", CLEAN_AER)
    assert odd.csv_row().count(",") == len(tel.COLUMNS) - 1


def test_read_aer_is_total_over_a_port_that_does_not_have_the_files(tmp_path: Path) -> None:
    assert tel.read_aer(None) == {}
    assert tel.read_aer(tmp_path) == dict.fromkeys(tel.AER_COUNTERS, "")
    (tmp_path / "aer_rootport_total_err_cor").write_text("7\n")
    assert tel.read_aer(tmp_path)["aer_rootport_total_err_cor"] == "7"


def test_the_log_writes_a_header_once_and_rotates_one_file(tmp_path: Path) -> None:
    log = tmp_path / "gpu-telemetry.csv"
    sample = tel.parse_sample("t", SMI_LINE, CLEAN_AER)
    tel.append(log, sample)
    tel.append(log, sample)
    lines = log.read_text().splitlines()
    assert lines[0] == ",".join(tel.COLUMNS)
    assert len(lines) == 3

    tel.append(log, sample, cap_bytes=1)  # forces a rotation before this row
    assert (tmp_path / "gpu-telemetry.csv.1").exists()
    assert log.read_text().splitlines()[0] == ",".join(tel.COLUMNS)


def test_no_nvidia_smi_yields_nothing_rather_than_raising() -> None:
    def missing(_cmd: list[str]) -> str:
        raise FileNotFoundError("nvidia-smi")

    assert tel.query_smi(missing) is None
    got = list(tel.samples(None, limit=3, runner=missing, sleep=lambda _s: None))
    assert got == []


def test_sampling_is_deterministic_given_a_clock_and_a_runner(tmp_path: Path) -> None:
    ticks = iter([1757471542.0, 1757471552.0])
    got = list(
        tel.samples(
            tmp_path,
            limit=2,
            runner=lambda _cmd: SMI_LINE,
            sleep=lambda _s: None,
            clock=lambda: next(ticks),
        )
    )
    assert len(got) == 2
    assert got[0].iso_time != got[1].iso_time
    assert all(s.temperature_c == "42" for s in got)
