"""PaddleOCR executor: image paths in, one JSON document out (lines with text, score, polygon)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--out", type=Path, required=True, help="JSON output path")
    args = parser.parse_args()
    from paddleocr import PaddleOCR  # slow import, kept after argparse so --help stays instant

    # oneDNN's PIR path aborts on this CPU build (ConvertPirAttribute2RuntimeAttribute), journal 2026-09-22.
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang=args.lang,
        enable_mkldnn=False,
    )
    results: list[dict[str, Any]] = []
    for image in args.images:
        lines: list[dict[str, Any]] = []
        for res in ocr.predict(input=str(image)):
            polys = res["rec_polys"] if len(res["rec_polys"]) else res["dt_polys"]
            for text, score, poly in zip(res["rec_texts"], res["rec_scores"], polys, strict=False):
                points = [[float(x), float(y)] for x, y in poly]
                lines.append({"text": str(text), "score": float(score), "polygon": points})
        results.append({"image": str(image), "lines": lines})
    args.out.write_text(json.dumps({"results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
