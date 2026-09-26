"""Capture a source: a Scoop WACZ (signed when a signer is given) or a PDF's bytes unaltered."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import ssl
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader

from content_factory.explainer.replay import free_port
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import Viewport

REPO = Path(__file__).resolve().parents[3]
SCOOP_DIR = REPO / "tools" / "scoop"
SCOOP_CLI = SCOOP_DIR / "node_modules" / "@harvard-lil" / "scoop" / "bin" / "cli.js"
DEFAULT_VIEWPORT = Viewport(width=1280, height=800)
# Scoop's default blocklist minus localhost, 127.0.0.0/8 and ::1, so a local fixture is reachable.
LOOPBACK_ALLOWED_BLOCKLIST = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/29",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "::/128",
    "::ffff:0:0/96",
    "100::/64",
    "64:ff9b::/96",
    "2001::/32",
    "2001:10::/28",
    "2001:db8::/32",
    "2002::/16",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)


class CaptureError(RuntimeError):
    """The capture did not complete or its artifact is inconsistent."""


@dataclass(frozen=True)
class WaczSignature:
    """signedData from datapackage-digest.json; anonymous means a bare public key and no domain."""

    hash: str
    software: str
    domain: str
    anonymous: bool


@dataclass(frozen=True)
class CaptureResult:
    url: str
    wacz_path: Path
    summary: dict[str, Any]
    artifact_sha256: str
    captured_at: str
    viewport: Viewport
    tls_certificate_sha256: str | None
    signed: bool
    signature_domain: str
    title: str
    capture_kind: Literal["wacz"] = "wacz"


@dataclass(frozen=True)
class PdfSpan:
    """One text run at its baseline origin in PDF user space (points, y up); no glyph widths."""

    text: str
    x: float
    y: float
    font_size: float


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str
    spans: tuple[PdfSpan, ...]


@dataclass(frozen=True)
class PdfCaptureResult:
    """needs_ocr: no page had positioned text, so quote rectangles need a rendered page."""

    url: str
    pdf_path: Path
    artifact_sha256: str
    captured_at: str
    pages: tuple[PdfPage, ...]
    needs_ocr: bool
    capture_kind: Literal["pdf"] = "pdf"


def capture_url(
    url: str,
    out_dir: Path,
    *,
    signing_url: str | None = None,
    signing_token: str | None = None,
    viewport: Viewport = DEFAULT_VIEWPORT,
    allow_loopback: bool = False,
    provenance_summary: bool = True,
    chromium_sandbox: bool = False,
    timeout_s: float = 300,
) -> CaptureResult:
    """Run Scoop on a URL; the WACZ lands in out_dir named by its own sha256."""
    if not SCOOP_CLI.is_file():
        msg = f"Scoop is not installed at {SCOOP_CLI}; run npm install in {SCOOP_DIR}"
        raise CaptureError(msg)
    node = shutil.which("node")
    if node is None:
        msg = "node is not on PATH"
        raise CaptureError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".scoop-") as tmp:
        work = Path(tmp)
        wacz = work / "capture.wacz"
        summary_path = work / "summary.json"
        attachments = work / "attachments"
        attachments.mkdir()
        argv = [
            node,
            str(SCOOP_CLI),
            url,
            *("--output", str(wacz)),
            *("--json-summary-output", str(summary_path)),
            *("--export-attachments-output", str(attachments)),
            *("--screenshot", "true"),
            *("--pdf-snapshot", "false"),
            *("--dom-snapshot", "true"),
            *("--capture-video-as-attachment", "false"),
            *("--capture-certificates-as-attachment", _flag(url.startswith("https://"))),
            *("--provenance-summary", _flag(provenance_summary)),
            *("--headless", "true"),
            # The sandbox needs unprivileged user namespaces, which AppArmor denies on this host
            # (journal 2026-09-22).
            *("--chromium-sandbox", _flag(chromium_sandbox)),
            *("--proxy-port", str(free_port())),
            *("--capture-window-x", str(viewport.width)),
            *("--capture-window-y", str(viewport.height)),
            *("--log-level", "warn"),
        ]
        if allow_loopback:
            argv += ["--blocklist", ",".join(LOOPBACK_ALLOWED_BLOCKLIST)]
        if signing_url:
            argv += ["--signing-url", signing_url]
            if signing_token:
                argv += ["--signing-token", signing_token]
        proc = subprocess.run(  # noqa: S603 — fixed argv; Scoop is a pinned separate process
            argv, cwd=SCOOP_DIR, capture_output=True, text=True, check=False, timeout=timeout_s
        )
        if proc.returncode != 0 or not wacz.is_file() or not summary_path.is_file():
            msg = f"Scoop failed ({proc.returncode}) for {url}: {proc.stderr.strip()[-2000:]}"
            raise CaptureError(msg)
        summary: dict[str, Any] = json.loads(summary_path.read_text())
        state = summary["states"][summary["state"]]
        if state not in {"COMPLETE", "PARTIAL"}:
            msg = f"Scoop ended in state {state} for {url}"
            raise CaptureError(msg)
        sha = file_sha256(wacz)
        final = out_dir / f"capture-{sha[:12]}.wacz"
        shutil.move(wacz, final)
        shutil.copyfile(summary_path, out_dir / f"capture-{sha[:12]}.summary.json")
        certificate = _certificate_sha256(attachments)
    signature = read_signature(final)
    return CaptureResult(
        url=url,
        wacz_path=final,
        summary=summary,
        artifact_sha256=sha,
        captured_at=str(summary.get("startedAt") or _now()),
        viewport=viewport,
        tls_certificate_sha256=certificate,
        signed=signature is not None,
        signature_domain=signature.domain if signature else "",
        title=str((summary.get("pageInfo") or {}).get("title") or ""),
    )


def load_capture(wacz_path: Path, url: str, viewport: Viewport = DEFAULT_VIEWPORT) -> CaptureResult:
    """Reopen a stored capture without touching the live page: summary beside it, cert inside."""
    wacz_path = Path(wacz_path)
    summary_path = wacz_path.with_suffix("").with_suffix(".summary.json")
    summary: dict[str, Any] = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    signature = read_signature(wacz_path)
    return CaptureResult(
        url=url,
        wacz_path=wacz_path,
        summary=summary,
        artifact_sha256=file_sha256(wacz_path),
        captured_at=str(summary.get("startedAt") or ""),
        viewport=viewport,
        tls_certificate_sha256=_archived_certificate_sha256(
            wacz_path, urlsplit(url).hostname or ""
        ),
        signed=signature is not None,
        signature_domain=signature.domain if signature else "",
        title=str((summary.get("pageInfo") or {}).get("title") or ""),
    )


def _archived_certificate_sha256(wacz_path: Path, host: str) -> str | None:
    """The leaf certificate Scoop archived as `file:///<host>.pem`, hashed like a fresh capture."""
    target = f"file:///{host}.pem".encode()
    with zipfile.ZipFile(wacz_path) as archive:
        for name in archive.namelist():
            if not (name.startswith("archive/") and name.endswith(".warc.gz")):
                continue
            data = gzip.decompress(archive.read(name))
            at = data.find(b"WARC-Target-URI: " + target)
            if at < 0:
                continue
            body = data[data.find(b"\r\n\r\n", at) + 4 :]
            pem = body[body.find(b"-----BEGIN CERTIFICATE-----") :].decode("ascii", "replace")
            der = ssl.PEM_cert_to_DER_cert(_first_pem_block(pem))
            return hashlib.sha256(der).hexdigest()
    return None


def read_signature(wacz_path: Path) -> WaczSignature | None:
    """The datapackage-digest.json signature, checked to cover the stored datapackage.json."""
    import zipfile

    with zipfile.ZipFile(wacz_path) as archive:
        digest = json.loads(archive.read("datapackage-digest.json"))
        stored = "sha256:" + hashlib.sha256(archive.read("datapackage.json")).hexdigest()
    signed = digest.get("signedData")
    if not signed:
        return None
    if digest.get("hash") != stored or signed.get("hash") != stored:
        msg = f"{wacz_path.name}: the signature does not cover the stored datapackage.json"
        raise CaptureError(msg)
    return WaczSignature(
        hash=stored,
        software=str(signed.get("software") or ""),
        domain=str(signed.get("domain") or ""),
        anonymous=bool(signed.get("publicKey")),
    )


def capture_pdf(
    url_or_path: str | Path, out_dir: Path, *, timeout_s: float = 60
) -> PdfCaptureResult:
    """Copy a PDF's bytes unaltered and read its text; positions come from pypdf's text visitor."""
    source = str(url_or_path)
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, follow_redirects=True, timeout=timeout_s)
        response.raise_for_status()
        data = response.content
    else:
        data = Path(source).read_bytes()
    if not data.startswith(b"%PDF-"):
        msg = f"{source} is not a PDF (no %PDF- header)"
        raise CaptureError(msg)
    sha = hashlib.sha256(data).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"capture-{sha[:12]}.pdf"
    pdf_path.write_bytes(data)
    reader = PdfReader(io.BytesIO(data))
    pages = tuple(_pdf_page(number, page) for number, page in enumerate(reader.pages, start=1))
    return PdfCaptureResult(
        url=source,
        pdf_path=pdf_path,
        artifact_sha256=sha,
        captured_at=_now(),
        pages=pages,
        needs_ocr=not any(page.spans for page in pages),
    )


def _pdf_page(number: int, page: Any) -> PdfPage:
    spans: list[PdfSpan] = []

    def visit(text: str, cm: Any, tm: Any, _font: Any, font_size: Any) -> None:
        if not text.strip():
            return
        # Text space -> user space: the text matrix's origin pushed through the current transform.
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        spans.append(PdfSpan(text=text, x=float(x), y=float(y), font_size=float(font_size or 0)))

    text = page.extract_text(visitor_text=visit) or ""
    return PdfPage(number=number, text=text, spans=tuple(spans))


def _certificate_sha256(attachments: Path) -> str | None:
    pems = sorted(attachments.glob("*.pem"))
    if not pems:
        return None
    # The first block is the leaf; Scoop stores the chain crip returned for the captured host.
    der = ssl.PEM_cert_to_DER_cert(_first_pem_block(pems[0].read_text()))
    return hashlib.sha256(der).hexdigest()


def _first_pem_block(text: str) -> str:
    start = text.find("-----BEGIN CERTIFICATE-----")
    end = text.find("-----END CERTIFICATE-----", start)
    if start < 0 or end < 0:
        msg = "certificate attachment holds no PEM block"
        raise CaptureError(msg)
    return text[start : end + len("-----END CERTIFICATE-----")] + "\n"


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
