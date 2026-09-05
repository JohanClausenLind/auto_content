from __future__ import annotations

import io

import numpy as np
from palette import color_for_id, palette_bytes
from PIL import Image
from pngio import encode_png


def test_palette_is_stable_and_distinct() -> None:
    assert color_for_id(0) == (0, 0, 0)
    colours = [color_for_id(i) for i in range(1, 255)]
    assert len(set(colours)) == len(colours)
    assert all(0 <= c <= 255 for rgb in colours for c in rgb)
    assert len(palette_bytes()) == 768


def test_indexed_png_round_trip_keeps_ids() -> None:
    index = np.zeros((8, 8), dtype=np.uint8)
    index[2:6, 2:6] = 3
    index[0, 0] = 254
    img = Image.fromarray(index, mode="P")
    img.putpalette(palette_bytes())
    data = encode_png(img)
    assert data == encode_png(img)
    back = Image.open(io.BytesIO(data))
    assert back.mode == "P"
    assert np.array_equal(np.asarray(back), index)
    assert back.getpalette()[3 * 3 : 3 * 3 + 3] == list(color_for_id(3))
