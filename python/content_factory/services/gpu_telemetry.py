"""Sample the card while it works, so the next Xid 79 is diagnosable instead of researched.

On 2026-09-10 at 03:12 this host's RTX 3090 left the PCIe bus in the eighth hour of a continuous
run (`AER: Uncorrectable ... TLP UnsupReq`, `Xid 79`, `Xid 154 Node Reboot Required`) and took the
desktop with it. Working out *why* afterwards meant reading forum threads, because the machine had
kept no record of its own state: no temperature, no power draw, no clocks, no throttle reasons, no
PCIe error counters. Every published account of this failure was solved by a different cause —
airflow in one, clock behaviour in another, a cable in a third — and the only thing they have in
common is that each person had to observe their own machine to find out which.

So this samples the handful of numbers that separate those explanations, at a cost of one
`nvidia-smi` call per interval, and appends them to a CSV that survives a reboot. It is not a
monitoring system; it is the log that should have existed on the night.

What it deliberately records, and why each one earns its column:

``temperature_c``, ``power_w``
    The two the forum accounts most often land on. A 3090 that throttles or sags under sustained
    load says so here long before it drops off the bus.
``sm_mhz``, ``mem_mhz``, ``pstate``
    Clock behaviour. One published fix for this exact failure was locking clocks
    (``nvidia-smi -lgc``), on the theory that the boost algorithm's voltage transitions are what
    the link cannot survive; without a clock trace that is untestable.
``throttle``, ``hw_slowdown``, ``sw_power_cap``, ``hw_thermal``
    The driver's own account of why it backed off, which is the difference between "hot" and
    "power-limited" and is not inferable from temperature alone.
``pcie_gen``
    The link speed the driver negotiated. A link that trains down under load is a link in trouble.
``aer_cor``, ``aer_nonfatal``, ``aer_fatal``
    The root port's PCIe error counters, read straight from sysfs and needing no root. These are
    the ones that saw the card leave: a single non-fatal was the whole event. Correctable errors
    are retried in hardware and invisible everywhere else, so a rising count here is the earliest
    warning this machine can give.

Memory junction temperature is **not** here, and its absence is the known blind spot: the 3090
puts GDDR6X on both sides of the board and it is the part that runs hottest, but this driver
reports ``Memory Current Temp: N/A`` for consumer cards. docs/gpu-hosts.md says what to do about
that.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

QUERY_FIELDS: tuple[str, ...] = (
    "temperature.gpu",
    "power.draw",
    "clocks.sm",
    "clocks.mem",
    "utilization.gpu",
    "memory.used",
    "pstate",
    "pcie.link.gen.current",
    "clocks_event_reasons.active",
    "clocks_event_reasons.hw_slowdown",
    "clocks_event_reasons.sw_power_cap",
    "clocks_event_reasons.hw_thermal_slowdown",
)
"""Asked for in one `nvidia-smi` call. `clocks_event_reasons` is the modern spelling of what the
driver used to call `clocks_throttle_reasons`; both exist on 5xx, only this one on newer."""

AER_COUNTERS: tuple[str, ...] = (
    "aer_rootport_total_err_cor",
    "aer_rootport_total_err_nonfatal",
    "aer_rootport_total_err_fatal",
)

COLUMNS: tuple[str, ...] = (
    "iso_time",
    "temperature_c",
    "power_w",
    "sm_mhz",
    "mem_mhz",
    "util_pct",
    "vram_mib",
    "pstate",
    "pcie_gen",
    "throttle",
    "hw_slowdown",
    "sw_power_cap",
    "hw_thermal",
    "aer_cor",
    "aer_nonfatal",
    "aer_fatal",
)

DEFAULT_INTERVAL_S = 10.0
DEFAULT_ROTATE_BYTES = 32 * 1024 * 1024
"""A row is ~110 bytes, so 32 MiB is about a month at ten seconds. One rotation is kept."""


@dataclass(frozen=True)
class Sample:
    iso_time: str
    temperature_c: str
    power_w: str
    sm_mhz: str
    mem_mhz: str
    util_pct: str
    vram_mib: str
    pstate: str
    pcie_gen: str
    throttle: str
    hw_slowdown: str
    sw_power_cap: str
    hw_thermal: str
    aer_cor: str
    aer_nonfatal: str
    aer_fatal: str

    def csv_row(self) -> str:
        values = asdict(self)
        return ",".join(values[c].replace(",", " ") for c in COLUMNS)

    @property
    def faulted(self) -> bool:
        """A PCIe error the hardware could not retry away. One of these was the whole event."""
        return _as_int(self.aer_nonfatal) > 0 or _as_int(self.aer_fatal) > 0


def _as_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def parse_sample(iso_time: str, smi_line: str, aer: dict[str, str]) -> Sample:
    """One `nvidia-smi` CSV line plus the sysfs counters, as a row. Pure, and total.

    Missing or unparsable fields become the empty string rather than raising: a sampler that dies
    on one odd line is a sampler that is not running when it is needed.
    """
    parts = [p.strip() for p in smi_line.split(",")]
    parts += [""] * (len(QUERY_FIELDS) - len(parts))
    return Sample(
        iso_time=iso_time,
        temperature_c=parts[0],
        power_w=parts[1],
        sm_mhz=parts[2],
        mem_mhz=parts[3],
        util_pct=parts[4],
        vram_mib=parts[5],
        pstate=parts[6],
        pcie_gen=parts[7],
        throttle=parts[8],
        hw_slowdown=parts[9],
        sw_power_cap=parts[10],
        hw_thermal=parts[11],
        aer_cor=aer.get("aer_rootport_total_err_cor", ""),
        aer_nonfatal=aer.get("aer_rootport_total_err_nonfatal", ""),
        aer_fatal=aer.get("aer_rootport_total_err_fatal", ""),
    )


def read_aer(port: Path | None) -> dict[str, str]:
    """The root port's error counters. Absent files give empty strings, not an exception."""
    if port is None:
        return {}
    out: dict[str, str] = {}
    for name in AER_COUNTERS:
        try:
            out[name] = (port / name).read_text().strip()
        except OSError:
            out[name] = ""
    return out


def query_smi(
    runner: Callable[[list[str]], str] | None = None, timeout: float = 15.0
) -> str | None:
    """The raw `nvidia-smi` line, or None when there is no card to ask."""
    cmd = [
        "nvidia-smi",
        f"--query-gpu={','.join(QUERY_FIELDS)}",
        "--format=csv,noheader,nounits",
    ]
    try:
        if runner is not None:
            out = runner(cmd)
        else:
            out = subprocess.check_output(cmd, text=True, timeout=timeout)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[0] if lines else None


def rotate_if_large(path: Path, cap_bytes: int = DEFAULT_ROTATE_BYTES) -> bool:
    """Keep one previous file. Returns whether a rotation happened."""
    try:
        if path.stat().st_size < cap_bytes:
            return False
    except OSError:
        return False
    path.replace(path.with_suffix(path.suffix + ".1"))
    return True


def append(path: Path, sample: Sample, *, cap_bytes: int = DEFAULT_ROTATE_BYTES) -> None:
    """Append one row, writing the header when the file is new or has just rotated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_if_large(path, cap_bytes)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write(",".join(COLUMNS) + "\n")
        fh.write(sample.csv_row() + "\n")


def samples(
    port: Path | None,
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    limit: int | None = None,
    runner: Callable[[list[str]], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Iterator[Sample]:
    """Yield samples forever, or `limit` of them. Skips a tick the driver would not answer."""
    taken = 0
    while limit is None or taken < limit:
        line = query_smi(runner)
        if line is not None:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(clock()))
            yield parse_sample(stamp, line, read_aer(port))
        taken += 1
        if limit is None or taken < limit:
            sleep(interval_s)


def default_log_path() -> Path:
    from content_factory.services.local import services_dir

    return services_dir() / "gpu-telemetry.csv"
