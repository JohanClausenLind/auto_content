"""OKLCH to sRGB, APCA contrast and OKLab distance, shared by the token generator and QC."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Oklch:
    lightness: float
    chroma: float
    hue_deg: float


def oklch_to_oklab(color: Oklch) -> tuple[float, float, float]:
    h = math.radians(color.hue_deg)
    return color.lightness, color.chroma * math.cos(h), color.chroma * math.sin(h)


def oklab_to_linear_srgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    return (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )


def linear_to_srgb_channel(value: float) -> float:
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * value ** (1 / 2.4) - 0.055


def srgb_channel_to_linear(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def in_gamut(rgb: tuple[float, float, float], tolerance: float = 1e-6) -> bool:
    return all(-tolerance <= c <= 1 + tolerance for c in rgb)


def oklch_to_srgb(color: Oklch) -> tuple[int, int, int]:
    """Convert, reducing chroma by bisection when the colour lies outside sRGB (hue and L kept)."""
    lo, hi = 0.0, color.chroma
    candidate = color
    rgb = oklab_to_linear_srgb(*oklch_to_oklab(candidate))
    if not in_gamut(rgb):
        for _ in range(32):
            mid = (lo + hi) / 2
            candidate = Oklch(color.lightness, mid, color.hue_deg)
            rgb = oklab_to_linear_srgb(*oklch_to_oklab(candidate))
            if in_gamut(rgb):
                lo = mid
            else:
                hi = mid
        rgb = oklab_to_linear_srgb(*oklch_to_oklab(Oklch(color.lightness, lo, color.hue_deg)))
    channels = [round(255 * min(1.0, max(0.0, linear_to_srgb_channel(c)))) for c in rgb]
    return channels[0], channels[1], channels[2]


def srgb_to_oklch(rgb: tuple[int, int, int]) -> Oklch:
    r, g, b = (srgb_channel_to_linear(c / 255) for c in rgb)
    lin_l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    lin_m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    lin_s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = math.cbrt(lin_l), math.cbrt(lin_m), math.cbrt(lin_s)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return Oklch(lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360)


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def rgb_of(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def delta_e_ok(a: Oklch, b: Oklch) -> float:
    """Euclidean distance in OKLab; 0.1 is a clearly different colour, below 0.04 is confusable."""
    la, aa, ba = oklch_to_oklab(a)
    lb, ab, bb = oklch_to_oklab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


# APCA-W3 0.0.98G-4g constants (Myndex); text-on-background polarity is picked from luminance.
_APCA = {
    "norm_bg": 0.56,
    "norm_txt": 0.57,
    "rev_txt": 0.62,
    "rev_bg": 0.65,
    "blk_thrs": 0.022,
    "blk_clmp": 1.414,
    "scale": 1.14,
    "lo_offset": 0.027,
    "lo_clip": 0.1,
    "delta_y_min": 0.0005,
}


def apca_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = ((c / 255) ** 2.4 for c in rgb)
    return 0.2126729 * r + 0.7151522 * g + 0.0721750 * b


def _soft_clamp(y: float) -> float:
    if y < _APCA["blk_thrs"]:
        return y + (_APCA["blk_thrs"] - y) ** _APCA["blk_clmp"]
    return y


def apca_lc(text: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    """Lightness contrast of text on background: positive dark-on-light, negative light-on-dark."""
    y_txt = _soft_clamp(apca_luminance(text))
    y_bg = _soft_clamp(apca_luminance(background))
    if abs(y_bg - y_txt) < _APCA["delta_y_min"]:
        return 0.0
    if y_bg > y_txt:
        sapc = (y_bg ** _APCA["norm_bg"] - y_txt ** _APCA["norm_txt"]) * _APCA["scale"]
        return 0.0 if sapc < _APCA["lo_clip"] else (sapc - _APCA["lo_offset"]) * 100
    sapc = (y_bg ** _APCA["rev_bg"] - y_txt ** _APCA["rev_txt"]) * _APCA["scale"]
    return 0.0 if sapc > -_APCA["lo_clip"] else (sapc + _APCA["lo_offset"]) * 100
