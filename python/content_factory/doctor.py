"""`content-factory doctor`: re-check services, runtimes, GPU, configuration; explain plainly.

Every check returns a typed result with a remediation sentence. No check needs the internet.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table


class Status(StrEnum):
    ok = "ok"
    warn = "warn"
    fail = "fail"
    skip = "skip"


class Check(BaseModel):
    name: str
    status: Status
    detail: str
    fix: str | None = None


class DoctorReport(BaseModel):
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.status != Status.fail for c in self.checks)

    def render(self, console: Console) -> None:
        table = Table(title="content-factory doctor", show_lines=False)
        table.add_column("check")
        table.add_column("status")
        table.add_column("detail")
        table.add_column("how to fix")
        colours = {
            Status.ok: "green",
            Status.warn: "yellow",
            Status.fail: "red",
            Status.skip: "dim",
        }
        for c in self.checks:
            table.add_row(
                c.name, f"[{colours[c.status]}]{c.status.value}[/]", c.detail, c.fix or ""
            )
        console.print(table)
        console.print("[green]All checks passed.[/]" if self.ok else "[red]Some checks failed.[/]")


@dataclass
class _Ctx:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str, fix: str | None = None) -> None:
        self.checks.append(Check(name=name, status=status, detail=detail, fix=fix))


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        return p.returncode, (p.stdout or p.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tool(
    ctx: _Ctx, name: str, version_args: list[str], fix: str, *, required: bool = True
) -> None:
    path = shutil.which(name)
    if not path:
        ctx.add(name, Status.fail if required else Status.warn, "not found on PATH", fix)
        return
    code, out = _run([name, *version_args])
    first = out.splitlines()[0] if out else ""
    ctx.add(
        name,
        Status.ok if code == 0 else Status.warn,
        first[:80] or path,
        None if code == 0 else fix,
    )


def run_doctor() -> DoctorReport:
    ctx = _Ctx()

    # Configuration validates (secrets never printed).
    try:
        from content_factory.config import get_settings

        settings = get_settings()
        ctx.add(
            "config",
            Status.ok,
            f"environment={settings.environment}, bind={settings.bind}:{settings.port}",
        )
    except Exception as exc:
        ctx.add(
            "config",
            Status.fail,
            str(exc)[:200],
            "Fix content-factory.yaml or CF__* environment overrides.",
        )
        settings = None

    _tool(
        ctx,
        "docker",
        ["--version"],
        "Install Docker Engine: https://docs.docker.com/engine/install/",
    )
    code, _ = _run(["docker", "compose", "version"])
    ctx.add(
        "docker compose",
        Status.ok if code == 0 else Status.fail,
        "available" if code == 0 else "missing",
        None if code == 0 else "Install the Docker Compose plugin.",
    )
    _tool(ctx, "ffmpeg", ["-version"], "sudo apt install ffmpeg (6.x or newer)")
    _tool(ctx, "ffprobe", ["-version"], "sudo apt install ffmpeg")
    _tool(ctx, "node", ["--version"], "Install Node 22 or 24 LTS (https://nodejs.org)")
    _tool(ctx, "pnpm", ["--version"], "corepack enable --install-directory ~/.local/bin")
    _tool(ctx, "uv", ["--version"], "curl -LsSf https://astral.sh/uv/install.sh | sh")
    _tool(ctx, "just", ["--version"], "uv tool install rust-just", required=False)
    _tool(
        ctx,
        "comfy",
        ["--version"],
        "uv tool install comfy-cli (optional: only for the ComfyUI sidecar)",
        required=False,
    )
    _tool(
        ctx,
        "tailscale",
        ["version"],
        "Optional: https://tailscale.com/download for tailnet access",
        required=False,
    )

    # GPU
    code, out = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]
    )
    if code == 0 and out:
        ctx.add("gpu", Status.ok, out.splitlines()[0])
    else:
        ctx.add(
            "gpu",
            Status.warn,
            "no NVIDIA GPU detected (CPU-only mode)",
            "GPU skills will be unavailable; cloud or CPU fallbacks apply per execution policy.",
        )

    # Services
    if settings is not None:
        db_url = os.environ.get(settings.database.url_env)
        if db_url:
            u = urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
            host, port = u.hostname or "127.0.0.1", u.port or 5432
            ok = _port_open(host, port)
            ctx.add(
                "postgres",
                Status.ok if ok else Status.fail,
                f"{host}:{port} {'reachable' if ok else 'unreachable'}",
                None if ok else "docker compose up -d --wait postgres",
            )
        else:
            ctx.add(
                "postgres",
                Status.fail,
                f"{settings.database.url_env} not set",
                "Copy .env.example to .env and set DATABASE_URL.",
            )
        host, _, port = settings.temporal.address.partition(":")
        ok = _port_open(host, int(port or 7233))
        ctx.add(
            "temporal",
            Status.ok if ok else Status.fail,
            f"{settings.temporal.address} {'reachable' if ok else 'unreachable'}",
            None if ok else "docker compose up -d --wait temporal",
        )
        if settings.comfyui.enabled:
            u = urlparse(settings.comfyui.endpoint)
            ok = _port_open(u.hostname or "127.0.0.1", u.port or 8188)
            ctx.add(
                "comfyui",
                Status.ok if ok else Status.warn,
                f"{settings.comfyui.endpoint} {'reachable' if ok else 'not running'}",
                None
                if ok
                else "Optional. Start with `comfy launch --background` when image skills are needed.",  # noqa: E501
            )
        if settings.search.provider == "searxng":
            u = urlparse(settings.search.endpoint)
            ok = _port_open(u.hostname or "127.0.0.1", u.port or 8083)
            ctx.add(
                "searxng",
                Status.ok if ok else Status.warn,
                f"{settings.search.endpoint} {'reachable' if ok else 'not running'}",
                None
                if ok
                else "Optional. `docker compose --profile search up -d`; offline fixtures are used otherwise.",  # noqa: E501
            )
        vault = os.environ.get(settings.token_vault.master_key_env)
        ctx.add(
            "token vault key",
            Status.ok if vault else Status.warn,
            "configured" if vault else f"{settings.token_vault.master_key_env} not set",
            None
            if vault
            else "Run `content-factory setup` to generate a master key before connecting accounts.",
        )

    # Disk
    usage = shutil.disk_usage(os.getcwd())
    free_gb = usage.free / 1024**3
    ctx.add(
        "disk",
        Status.ok if free_gb > 20 else Status.warn,
        f"{free_gb:.0f} GB free in the project volume",
        None if free_gb > 20 else "Renders and models need space; free at least 20 GB.",
    )

    return DoctorReport(checks=ctx.checks)


def report_json(report: DoctorReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)


__all__: list[str] = ["Check", "DoctorReport", "Status", "report_json", "run_doctor"]
_: Any = None
