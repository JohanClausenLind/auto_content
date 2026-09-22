"""Gates D2-D4: quotes resolve on the replay, OCR guards pixels, replay ignores the live page."""

from __future__ import annotations

import base64
import json
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image

from content_factory.explainer.capture import CaptureResult, capture_url, read_signature
from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.ocr import verify_manifest
from content_factory.explainer.passages import QuoteRequest, ResolvedPage, Tile, resolve_quotes
from content_factory.explainer.replay import ReplayServer
from content_factory.explainer.sources import SourceInfo, build_manifest
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import CaptureQuote, SourceCaptureManifest, Viewport
from tests.unit.explainer_site import FixtureSite

pytestmark = pytest.mark.render

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / "fixtures" / "explainer" / "source_site"
VIEWPORT = Viewport(width=1280, height=800)
BELOW_FOLD = (
    "The measurements do not support the claim that a faster tram shortens the trip; "
    "the gain is spent at the stops."
)
MULTI_LINE = (
    "What the model does show, without any of those refinements, is that if you hold the moving "
    "time exactly at its timetabled value and let only the standing time vary as measured, you "
    "reproduce the whole shape of the observed lateness curve, including the place where it bends "
    "and the size of the gap at the terminus, and that is the finding a procurement decision "
    "should be answering to."
)
REPEATED = "dwell time, not speed, sets the timetable"
MISSING = "Late trams get later; this is the bunching effect, and every driver knows it."
REQUESTS = (
    QuoteRequest(BELOW_FOLD, claim_ids=("clm_belowfold01",)),
    QuoteRequest(MULTI_LINE),
    QuoteRequest(REPEATED, occurrence_index=2),
)
SOURCE = SourceInfo("src_fixturetram01", publisher="Transit Notes", author="Fixture")


@pytest.fixture(scope="module")
def site() -> Iterator[FixtureSite]:
    with FixtureSite(SITE) as served:
        yield served


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("capture")


@pytest.fixture(scope="module")
def captured(site: FixtureSite, out_dir: Path) -> CaptureResult:
    return capture_url(
        site.url, out_dir, viewport=VIEWPORT, allow_loopback=True, provenance_summary=False
    )


@pytest.fixture(scope="module")
def replay(captured: CaptureResult) -> Iterator[ReplayServer]:
    with ReplayServer(captured.wacz_path) as server:
        yield server


@pytest.fixture(scope="module")
def resolved(replay: ReplayServer, site: FixtureSite, out_dir: Path) -> ResolvedPage:
    url = replay.replay_url(site.url)
    return resolve_quotes(url, REQUESTS, viewport=VIEWPORT, tiles_dir=out_dir / "tiles")


@pytest.fixture(scope="module")
def manifest(captured: CaptureResult, resolved: ResolvedPage) -> SourceCaptureManifest:
    return verify_manifest(build_manifest(SOURCE, captured, resolved), resolved.tiles)


def _quote(page: ResolvedPage, text: str) -> CaptureQuote:
    return next(q for q in page.quotes if q.text == text)


def _blank_copy(tile: Tile, into: Path) -> Path:
    with Image.open(tile.path) as image:
        path = into / tile.path.name
        Image.new("RGB", image.size, "white").save(path)
    return path


def test_repeated_phrase_without_index_is_ambiguous_naming_three(
    replay: ReplayServer, site: FixtureSite, tmp_path: Path
) -> None:
    url = replay.replay_url(site.url)
    with pytest.raises(EpisodeInvalidError) as info:
        resolve_quotes(url, [QuoteRequest(REPEATED)], viewport=VIEWPORT, tiles_dir=tmp_path)
    (issue,) = info.value.issues
    assert issue.kind == "invalid_reference"
    assert "passage_ambiguous" in issue.message
    assert "3 times" in issue.message
    assert "occurrence_index (0..2)" in issue.fix


def test_occurrence_index_two_lands_in_the_method_notes_section(resolved: ResolvedPage) -> None:
    quote = _quote(resolved, REPEATED)
    section = next(s for s in resolved.sections if s.section_id == quote.section_id)
    assert quote.occurrence_index == 2
    assert section.heading.startswith("8. Method notes")
    assert quote.line_rects[0].y > section.scroll_y_px


def test_multi_line_passage_yields_stacked_line_rects(resolved: ResolvedPage) -> None:
    rects = _quote(resolved, MULTI_LINE).line_rects
    assert len(rects) >= 2
    ys = [r.y for r in rects]
    assert ys == sorted(ys)
    assert len(set(ys)) == len(ys)


def test_below_the_fold_passage_sits_past_the_first_viewport(resolved: ResolvedPage) -> None:
    quote = _quote(resolved, BELOW_FOLD)
    assert quote.line_rects[0].y > VIEWPORT.height
    assert quote.claim_ids == ("clm_belowfold01",)
    assert "faster tram" in quote.text
    assert quote.context_before.endswith("after cleaning.")


def test_missing_passage_names_the_nearest_text(
    replay: ReplayServer, site: FixtureSite, tmp_path: Path
) -> None:
    url = replay.replay_url(site.url)
    with pytest.raises(EpisodeInvalidError) as info:
        resolve_quotes(url, [QuoteRequest(MISSING)], viewport=VIEWPORT, tiles_dir=tmp_path)
    (issue,) = info.value.issues
    assert issue.kind == "invalid_reference"
    assert "passage_not_found" in issue.message
    assert "every operator knows it" in issue.message


def test_blank_tiles_fail_ocr_with_the_mismatch_message(
    manifest: SourceCaptureManifest, resolved: ResolvedPage, tmp_path: Path
) -> None:
    blank = tuple(Tile(_blank_copy(tile, tmp_path), tile.y_px) for tile in resolved.tiles)
    with pytest.raises(EpisodeInvalidError) as info:
        verify_manifest(manifest, blank)
    assert len(info.value.issues) == len(manifest.quotes)
    assert all(issue.kind == "invalid_value" for issue in info.value.issues)
    assert "OCR read '' but the manifest says" in str(info.value)
    assert "never accept a screenshot whose pixels do not match its text" in str(info.value)


def test_altered_quote_text_is_rejected_by_ocr(
    manifest: SourceCaptureManifest, resolved: ResolvedPage
) -> None:
    original = next(q for q in manifest.quotes if q.text == BELOW_FOLD)
    altered = original.model_copy(
        update={
            "text": BELOW_FOLD.replace("do not support", "do support"),
            "ocr_verified": False,
            "ocr_similarity": None,
        }
    )
    tampered = SourceCaptureManifest.model_validate(
        manifest.model_copy(update={"quotes": (altered,)}).model_dump()
    )
    with pytest.raises(EpisodeInvalidError) as info:
        verify_manifest(tampered, resolved.tiles)
    (issue,) = info.value.issues
    assert issue.kind == "invalid_value"
    assert f"capture {manifest.capture_id} quote {altered.quote_id}: OCR read '" in issue.message
    assert "but the manifest says 'The measurements do support" in issue.message
    assert "words only in the OCR: 'not'" in issue.message


def test_replay_resolves_identically_after_the_live_page_changed(
    site: FixtureSite,
    replay: ReplayServer,
    resolved: ResolvedPage,
    manifest: SourceCaptureManifest,
    captured: CaptureResult,
    tmp_path: Path,
) -> None:
    site.serve("article-changed.html")
    try:
        live = httpx.get(site.url, timeout=10).text
        assert "the gain is kept all the way to the terminus" in live
        assert BELOW_FOLD not in live
        url = replay.replay_url(site.url)
        again = resolve_quotes(url, REQUESTS, viewport=VIEWPORT, tiles_dir=tmp_path / "tiles")
    finally:
        site.serve("article.html")
    assert again.text_sha256 == resolved.text_sha256
    assert again.page_height_px == resolved.page_height_px
    assert [q.line_rects for q in again.quotes] == [q.line_rects for q in resolved.quotes]
    assert [q.locator for q in again.quotes] == [q.locator for q in resolved.quotes]
    assert _quote(again, BELOW_FOLD).text == BELOW_FOLD
    SourceCaptureManifest.model_validate_json(manifest.model_dump_json())
    assert manifest.capture_kind == "wacz"
    assert manifest.artifact_sha256 == file_sha256(captured.wacz_path)
    assert manifest.signed is False
    assert manifest.signature_domain == ""
    assert read_signature(captured.wacz_path) is None
    assert manifest.tls_certificate_sha256 is None
    assert all(q.ocr_verified and (q.ocr_similarity or 0) >= 0.9 for q in manifest.quotes)


class _AnonymousSigner:
    """authsign-compatible /sign with a throwaway P-256 key: WACZ signing spec 0.1.0, anonymous."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self._server: ThreadingHTTPServer | None = None
        self.url = ""

    def __enter__(self) -> Self:
        key = self.key
        spki = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                signature = key.sign(body["hash"].encode(), ec.ECDSA(hashes.SHA256()))
                payload = {
                    "hash": body["hash"],
                    "created": body["created"],
                    "software": "test-anonymous-signer 0.0.0",
                    "signature": base64.b64encode(signature).decode(),
                    "publicKey": base64.b64encode(spki).decode(),
                    "version": "0.1.0",
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/sign"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()


def test_signing_endpoint_signature_lands_in_the_wacz(site: FixtureSite, tmp_path: Path) -> None:
    with _AnonymousSigner() as signer:
        result = capture_url(
            site.url,
            tmp_path,
            viewport=VIEWPORT,
            allow_loopback=True,
            provenance_summary=False,
            signing_url=signer.url,
        )
    signature = read_signature(result.wacz_path)
    assert result.signed is True
    assert signature is not None
    assert signature.anonymous is True
    assert signature.domain == "" and result.signature_domain == ""
    with zipfile.ZipFile(result.wacz_path) as archive:
        signed = json.loads(archive.read("datapackage-digest.json"))["signedData"]
    signer.key.public_key().verify(
        base64.b64decode(signed["signature"]), signed["hash"].encode(), ec.ECDSA(hashes.SHA256())
    )
