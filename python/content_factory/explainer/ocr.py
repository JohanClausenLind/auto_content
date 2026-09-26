"""OCR verification: the pixels under a quote's line rects must read as the quote's text."""

from __future__ import annotations

import difflib
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from PIL import Image

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.passages import Tile
from content_factory.schemas.explainer import CaptureQuote, PageRect, SourceCaptureManifest

REPO = Path(__file__).resolve().parents[3]
PADDLE_PYTHON = REPO / ".venvs" / "paddleocr" / "bin" / "python"
PADDLE_SCRIPT = REPO / "skills" / "ocr" / "paddle_detect.py"
# 0.90 is the starting threshold (journal 2026-09-22); compare_texts also fails any dropped word.
OCR_THRESHOLD = 0.90
WORD_THRESHOLD = 0.75
PADDING_PX = 6
LINE_GAP_PX = 10
TARGET_LINE_PX = 40
_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u00b4": "'",
        "`": "'",
    }
)
_DASHES = re.compile("[\u2010-\u2015\u2212]")


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class OcrVerdict:
    similarity: float
    ocr_text: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class TextComparison:
    """Character similarity plus the words one side has and the other lacks."""

    similarity: float
    only_expected: tuple[str, ...]
    only_ocr: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.similarity >= OCR_THRESHOLD and not self.only_expected and not self.only_ocr


def normalize_text(text: str) -> str:
    """Fold what OCR cannot be blamed for: quote and dash glyphs, case, whitespace, NFKC forms."""
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    folded = _DASHES.sub("-", folded).replace("…", "...")
    return " ".join(folded.split()).casefold()


def compare_texts(expected: str, ocr: str) -> TextComparison:
    """Similarity is the character ratio; whole words missing or extra on either side are listed."""
    a, b = normalize_text(expected), normalize_text(ocr)
    similarity = difflib.SequenceMatcher(None, a, b).ratio() if a or b else 1.0
    words_a, words_b = a.split(), b.split()
    only_a: list[str] = []
    only_b: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, words_a, words_b).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            for wa, wb in zip(words_a[i1:i2], words_b[j1:j2], strict=True):
                if difflib.SequenceMatcher(None, wa, wb).ratio() < WORD_THRESHOLD:
                    only_a.append(wa)
                    only_b.append(wb)
            continue
        only_a.extend(words_a[i1:i2])
        only_b.extend(words_b[j1:j2])
    # Punctuation-only tokens carry no meaning; padding often catches a neighbour's full stop.
    only_a = [w for w in only_a if any(ch.isalnum() for ch in w)]
    only_b = [w for w in only_b if any(ch.isalnum() for ch in w)]
    return TextComparison(round(similarity, 4), tuple(only_a), tuple(only_b))


def tile_spans(
    tiles: Sequence[tuple[int, int]], y0: int, y1: int
) -> list[tuple[int, int, int, int]]:
    """Pieces (tile, src y0, src y1, dst y) covering page rows y0..y1 from (top, height) tiles."""
    pieces: list[tuple[int, int, int, int]] = []
    cursor = y0
    for index, (top, height) in sorted(enumerate(tiles), key=lambda item: item[1][0]):
        if cursor >= y1:
            break
        start, end = max(top, cursor), min(top + height, y1)
        if start != cursor or end <= start:
            continue
        pieces.append((index, start, end, cursor))
        cursor = end
    if cursor < y1:
        msg = f"page rows {cursor}..{y1} are not covered by any tile"
        raise ValueError(msg)
    return pieces


def crop_rect(tiles: Sequence[Tile], rect: PageRect, padding_px: int = PADDING_PX) -> Image.Image:
    """Page pixels under a rect, stitched across tile boundaries, padded and clamped to the page."""
    images = [Image.open(tile.path).convert("RGB") for tile in tiles]
    geometry = [(tile.y_px, image.height) for tile, image in zip(tiles, images, strict=True)]
    page_width = min(image.width for image in images)
    page_bottom = max(top + height for top, height in geometry)
    x0 = max(0, math.floor(rect.x - padding_px))
    x1 = min(page_width, math.ceil(rect.x + rect.width + padding_px))
    y0 = max(0, math.floor(rect.y - padding_px))
    y1 = min(page_bottom, math.ceil(rect.y + rect.height + padding_px))
    canvas = Image.new("RGB", (max(1, x1 - x0), max(1, y1 - y0)), "white")
    for index, sy0, sy1, dy in tile_spans(geometry, y0, y1):
        top = geometry[index][0]
        canvas.paste(images[index].crop((x0, sy0 - top, x1, sy1 - top)), (0, dy - y0))
    return canvas


def quote_image(tiles: Sequence[Tile], quote: CaptureQuote) -> Image.Image:
    """One quote's line crops stacked and upscaled so a line is about TARGET_LINE_PX tall."""
    crops = [crop_rect(tiles, rect) for rect in quote.line_rects]
    width = max(crop.width for crop in crops)
    height = sum(crop.height for crop in crops) + LINE_GAP_PX * (len(crops) + 1)
    stacked = Image.new("RGB", (width + 2 * LINE_GAP_PX, height), "white")
    y = LINE_GAP_PX
    for crop in crops:
        stacked.paste(crop, (LINE_GAP_PX, y))
        y += crop.height + LINE_GAP_PX
    line_px = median(rect.height for rect in quote.line_rects)
    scale = min(4, max(1, math.ceil(TARGET_LINE_PX / max(1.0, line_px))))
    if scale == 1:
        return stacked
    return stacked.resize((stacked.width * scale, stacked.height * scale), Image.Resampling.LANCZOS)


def run_paddle(images: Sequence[Path], *, timeout_s: float = 900) -> list[list[OcrLine]]:
    """One PaddleOCR process for all images, in its own venv; lines come back in reading order."""
    if not PADDLE_PYTHON.is_file():
        msg = f"PaddleOCR venv missing at {PADDLE_PYTHON}"
        raise RuntimeError(msg)
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        out = Path(tmp) / "ocr.json"
        argv = [
            str(PADDLE_PYTHON),
            "-W",
            "ignore",
            str(PADDLE_SCRIPT),
            *map(str, images),
            "--out",
            str(out),
        ]
        proc = subprocess.run(  # noqa: S603 — fixed argv into the isolated OCR venv
            argv, capture_output=True, text=True, check=False, timeout=timeout_s
        )
        if proc.returncode != 0 or not out.is_file():
            msg = f"paddle_detect.py failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}"
            raise RuntimeError(msg)
        payload = json.loads(out.read_text())
    results: list[list[OcrLine]] = []
    for entry in payload["results"]:
        lines = [
            OcrLine(
                str(line["text"]),
                float(line["score"]),
                tuple((float(x), float(y)) for x, y in line["polygon"]),
            )
            for line in entry["lines"]
        ]
        results.append(sorted(lines, key=_reading_order))
    return results


def verify_quotes(tiles: Sequence[Tile], quotes: Sequence[CaptureQuote]) -> dict[str, OcrVerdict]:
    """Every quote's crop through one OCR run; blank crops and text drift both fail."""
    if not quotes:
        return {}
    with tempfile.TemporaryDirectory(prefix="ocr-crops-") as tmp:
        paths: list[Path] = []
        for quote in quotes:
            path = Path(tmp) / f"{quote.quote_id}.png"
            quote_image(tiles, quote).save(path)
            paths.append(path)
        recognised = run_paddle(paths)
    verdicts: dict[str, OcrVerdict] = {}
    for quote, lines in zip(quotes, recognised, strict=True):
        ocr_text = " ".join(line.text for line in lines)
        verdicts[quote.quote_id] = judge(quote.text, ocr_text)
    return verdicts


def verify_quote(tiles: Sequence[Tile], quote: CaptureQuote) -> OcrVerdict:
    return verify_quotes(tiles, [quote])[quote.quote_id]


def judge(expected: str, ocr_text: str) -> OcrVerdict:
    if not ocr_text.strip():
        reason = (
            f"OCR read '' but the manifest says '{expected}' (similarity 0.00; no text recognised)"
        )
        return OcrVerdict(0.0, "", False, reason)
    comparison = compare_texts(expected, ocr_text)
    if comparison.passed:
        return OcrVerdict(comparison.similarity, ocr_text, True, "OCR text matches the quote")
    detail = f"similarity {comparison.similarity:.2f}"
    if comparison.only_ocr:
        detail += f"; words only in the OCR: {', '.join(repr(w) for w in comparison.only_ocr)}"
    if comparison.only_expected:
        detail += (
            f"; words only in the manifest: {', '.join(repr(w) for w in comparison.only_expected)}"
        )
    reason = f"OCR read '{ocr_text}' but the manifest says '{expected}' ({detail})"
    return OcrVerdict(comparison.similarity, ocr_text, False, reason)


def verify_manifest(
    manifest: SourceCaptureManifest, tiles: Sequence[Tile]
) -> SourceCaptureManifest:
    """The manifest with every quote OCR-verified, or one error naming every quote that is not."""
    verdicts = verify_quotes(tiles, manifest.quotes)
    fix = (
        "re-capture the page or fix the quote text; never accept a screenshot whose pixels "
        "do not match its text."
    )
    issues: list[ContractIssue] = []
    for quote in manifest.quotes:
        verdict = verdicts[quote.quote_id]
        if verdict.passed:
            continue
        where = f"capture {manifest.capture_id} quote {quote.quote_id}"
        message = f"{where}: {verdict.reason}."
        ids = (manifest.capture_id, quote.quote_id)
        issues.append(ContractIssue("invalid_value", where, message, fix, ids))
    if issues:
        raise EpisodeInvalidError(issues)
    quotes = tuple(
        quote.model_copy(
            update={"ocr_verified": True, "ocr_similarity": verdicts[quote.quote_id].similarity}
        )
        for quote in manifest.quotes
    )
    return SourceCaptureManifest.model_validate(
        manifest.model_copy(update={"quotes": quotes}).model_dump()
    )


def _reading_order(line: OcrLine) -> tuple[float, float]:
    top = min(y for _, y in line.polygon)
    left = min(x for x, _ in line.polygon)
    return (round(top / 8), left)
