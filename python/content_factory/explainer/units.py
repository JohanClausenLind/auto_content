"""Units for claim arithmetic: a few dimensions, exact factors, no external dependency."""

from __future__ import annotations

from typing import Final

from content_factory.schemas.explainer import Quantity, UncertaintyKind

# Base dimensions; energy is power x time, so "TWh/h" comes out as power with no special case.
BASE: Final[tuple[str, ...]] = ("time", "power", "count", "SEK", "EUR", "USD", "length", "mass")
Dim = tuple[int, ...]


class UnitError(ValueError):
    """A unit the system does not know, or two dimensions that do not combine."""


def _dim(**exponents: int) -> Dim:
    return tuple(exponents.get(name, 0) for name in BASE)


_NONE = _dim()
_TIME = _dim(time=1)
_POWER = _dim(power=1)
_ENERGY = _dim(power=1, time=1)

# unit -> (factor to its dimension's base unit, dimension). Time is based on ms so every time
# factor is an integer and the Gate B1 sums stay exact; percent is the only fractional factor.
UNITS: Final[dict[str, tuple[float, Dim]]] = {
    "ms": (1.0, _TIME),
    "s": (1e3, _TIME),
    "min": (6e4, _TIME),
    "h": (3.6e6, _TIME),
    "day": (8.64e7, _TIME),
    "W": (1.0, _POWER),
    "kW": (1e3, _POWER),
    "MW": (1e6, _POWER),
    "GW": (1e9, _POWER),
    "Wh": (3.6e6, _ENERGY),
    "kWh": (3.6e9, _ENERGY),
    "MWh": (3.6e12, _ENERGY),
    "GWh": (3.6e15, _ENERGY),
    "TWh": (3.6e18, _ENERGY),
    "%": (0.01, _NONE),
    "pp": (0.01, _NONE),
    "fraction": (1.0, _NONE),
    "ratio": (1.0, _NONE),
    "x": (1.0, _NONE),
    "count": (1.0, _dim(count=1)),
    "SEK": (1.0, _dim(SEK=1)),
    "EUR": (1.0, _dim(EUR=1)),
    "USD": (1.0, _dim(USD=1)),
    "m": (1.0, _dim(length=1)),
    "km": (1e3, _dim(length=1)),
    "kg": (1.0, _dim(mass=1)),
    "t": (1e3, _dim(mass=1)),
}

DIMENSION_NAMES: Final[dict[Dim, str]] = {
    _NONE: "dimensionless",
    _TIME: "time",
    _POWER: "power",
    _ENERGY: "energy",
    _dim(count=1): "count",
    _dim(SEK=1): "SEK",
    _dim(EUR=1): "EUR",
    _dim(USD=1): "USD",
    _dim(length=1): "length",
    _dim(mass=1): "mass",
}

# Spoken or written unit words -> unit symbol; symbols map to themselves, "x" and "times" to ratio.
UNIT_WORDS: Final[dict[str, str]] = {
    **{unit: unit for unit in UNITS},
    "millisecond": "ms",
    "milliseconds": "ms",
    "second": "s",
    "seconds": "s",
    "minute": "min",
    "minutes": "min",
    "hour": "h",
    "hours": "h",
    "days": "day",
    "percent": "%",
    "per cent": "%",
    "percentage": "%",
    "percentage point": "pp",
    "percentage points": "pp",
    "watt-hours": "Wh",
    "watt hours": "Wh",
    "kilowatt-hours": "kWh",
    "kilowatt hours": "kWh",
    "megawatt-hours": "MWh",
    "megawatt hours": "MWh",
    "gigawatt-hours": "GWh",
    "gigawatt hours": "GWh",
    "terawatt-hours": "TWh",
    "terawatt hours": "TWh",
    "watts": "W",
    "kilowatts": "kW",
    "megawatts": "MW",
    "gigawatts": "GW",
    "times": "ratio",
    "x": "ratio",
    "×": "ratio",  # noqa: RUF001  the multiplication sign is how typed narration writes "times"
    "metre": "m",
    "metres": "m",
    "meter": "m",
    "meters": "m",
    "kilometre": "km",
    "kilometres": "km",
    "kilometer": "km",
    "kilometers": "km",
    "kilogram": "kg",
    "kilograms": "kg",
    "tonne": "t",
    "tonnes": "t",
    "kronor": "SEK",
    "euro": "EUR",
    "euros": "EUR",
    "dollar": "USD",
    "dollars": "USD",
}
_WORDS_LOWER: Final[dict[str, str]] = {
    word.lower(): unit for word, unit in UNIT_WORDS.items() if word not in UNITS
}
_TRAILING_PUNCTUATION = ".,;:!?)\"'"
_UNCERTAINTY_RANK: Final[dict[str, int]] = {"exact": 0, "rounded": 1, "estimate": 2, "interval": 3}


def unit_for_word(word: str) -> str | None:
    """The unit a token names, or None; symbols are case-sensitive, words are not."""
    bare = word.rstrip(_TRAILING_PUNCTUATION)
    return UNIT_WORDS.get(bare) or _WORDS_LOWER.get(bare.lower())


def dimension_of(unit: str) -> str:
    return _dimension_name(_parse(unit)[1])


def same_dimension(a: Quantity | str, b: Quantity | str) -> bool:
    return _parse(_unit_of(a))[1] == _parse(_unit_of(b))[1]


def convert(q: Quantity, unit: str) -> Quantity:
    """The same quantity in another unit of the same dimension; interval bounds convert too."""
    from_factor, from_dim = _parse(q.unit)
    to_factor, to_dim = _parse(unit)
    if from_dim != to_dim:
        msg = (
            f"cannot convert {q.unit} to {unit}: {_dimension_name(from_dim)} vs "
            f"{_dimension_name(to_dim)}; convert or fix the claim unit"
        )
        raise UnitError(msg)
    ratio = from_factor / to_factor
    return Quantity(
        magnitude=q.magnitude * ratio,
        unit=unit,
        uncertainty=q.uncertainty,
        low=None if q.low is None else q.low * ratio,
        high=None if q.high is None else q.high * ratio,
    )


def magnitude_in(q: Quantity, unit: str) -> float:
    return convert(q, unit).magnitude


def add(a: Quantity, b: Quantity) -> Quantity:
    """a + b in a's unit."""
    _require_same_dimension(a, b, f"cannot add {a.unit} to {b.unit}")
    return _linear(a, b, sign=1.0)


def sub(a: Quantity, b: Quantity) -> Quantity:
    """a - b in a's unit."""
    _require_same_dimension(a, b, f"cannot subtract {b.unit} from {a.unit}")
    return _linear(a, b, sign=-1.0)


def mul(a: Quantity, b: Quantity) -> Quantity:
    """a * b; the unit is an operand's when the dimensions allow, else "ratio" or a composed one."""
    a_factor, a_dim = _parse(a.unit)
    b_factor, b_dim = _parse(b.unit)
    dim = tuple(x + y for x, y in zip(a_dim, b_dim, strict=True))
    a_low, a_high = _bounds(a, a_factor)
    b_low, b_high = _bounds(b, b_factor)
    corners = [x * y for x in (a_low, a_high) for y in (b_low, b_high)]
    a_num, a_den = _split(a.unit)
    b_num, b_den = _split(b.unit)
    unit = _result_unit(dim, a, a_dim, b, b_dim, _compose(a_num + b_num, a_den + b_den))
    base = (a.magnitude * a_factor) * (b.magnitude * b_factor)
    return _from_base(base, min(corners), max(corners), unit, _combined(a, b))


def div(a: Quantity, b: Quantity) -> Quantity:
    """a / b; the unit is an operand's when the dimensions allow, else "ratio" or a composed one."""
    a_factor, a_dim = _parse(a.unit)
    b_factor, b_dim = _parse(b.unit)
    dim = tuple(x - y for x, y in zip(a_dim, b_dim, strict=True))
    a_low, a_high = _bounds(a, a_factor)
    b_low, b_high = _bounds(b, b_factor)
    if b_low <= 0.0 <= b_high:
        msg = f"cannot divide by {b.magnitude} {b.unit}: the divisor is or spans zero"
        raise ValueError(msg)
    corners = [x / y for x in (a_low, a_high) for y in (b_low, b_high)]
    a_num, a_den = _split(a.unit)
    b_num, b_den = _split(b.unit)
    unit = _result_unit(dim, a, a_dim, b, b_dim, _compose(a_num + b_den, a_den + b_num))
    base = (a.magnitude * a_factor) / (b.magnitude * b_factor)
    return _from_base(base, min(corners), max(corners), unit, _combined(a, b))


def _linear(a: Quantity, b: Quantity, sign: float) -> Quantity:
    b_in_a = convert(b, a.unit)
    a_low, a_high = _bounds(a, 1.0)
    b_low, b_high = _bounds(b_in_a, 1.0)
    low = a_low + sign * (b_high if sign < 0 else b_low)
    high = a_high + sign * (b_low if sign < 0 else b_high)
    magnitude = a.magnitude + sign * b_in_a.magnitude
    return _from_base(magnitude, low, high, a.unit, _combined(a, b), factor=1.0)


def _from_base(
    base: float,
    low: float,
    high: float,
    unit: str,
    uncertainty: UncertaintyKind,
    factor: float | None = None,
) -> Quantity:
    scale = _parse(unit)[0] if factor is None else factor
    interval = uncertainty == "interval"
    return Quantity(
        magnitude=base / scale,
        unit=unit,
        uncertainty=uncertainty,
        low=low / scale if interval else None,
        high=high / scale if interval else None,
    )


def _bounds(q: Quantity, factor: float) -> tuple[float, float]:
    if q.low is not None and q.high is not None:
        return q.low * factor, q.high * factor
    return q.magnitude * factor, q.magnitude * factor


def _combined(a: Quantity, b: Quantity) -> UncertaintyKind:
    return max((a.uncertainty, b.uncertainty), key=lambda kind: _UNCERTAINTY_RANK[kind])


def _result_unit(dim: Dim, a: Quantity, a_dim: Dim, b: Quantity, b_dim: Dim, composed: str) -> str:
    if dim == a_dim:
        return a.unit
    if dim == b_dim:
        return b.unit
    if dim == _NONE:
        return "ratio"
    return composed


def _require_same_dimension(a: Quantity, b: Quantity, what: str) -> None:
    a_dim, b_dim = _parse(a.unit)[1], _parse(b.unit)[1]
    if a_dim != b_dim:
        msg = (
            f"{what}: {_dimension_name(a_dim)} vs {_dimension_name(b_dim)}; "
            "convert or fix the claim unit"
        )
        raise UnitError(msg)


def _unit_of(value: Quantity | str) -> str:
    return value if isinstance(value, str) else value.unit


def _parse(unit: str) -> tuple[float, Dim]:
    """Factor and dimension of a symbol or a composed unit; "/" divides everything after it."""
    numerator, denominator = _split(unit)
    factor, dim = 1.0, _NONE
    for symbol in numerator:
        symbol_factor, symbol_dim = _atom(symbol)
        factor *= symbol_factor
        dim = tuple(x + y for x, y in zip(dim, symbol_dim, strict=True))
    for symbol in denominator:
        symbol_factor, symbol_dim = _atom(symbol)
        factor /= symbol_factor
        dim = tuple(x - y for x, y in zip(dim, symbol_dim, strict=True))
    return factor, dim


def _split(unit: str) -> tuple[list[str], list[str]]:
    head, _, tail = unit.partition("/")
    return _symbols(head), _symbols(tail.replace("/", "*"))


def _symbols(part: str) -> list[str]:
    return [s for s in part.replace("·", "*").split("*") if s]


def _atom(symbol: str) -> tuple[float, Dim]:
    if symbol not in UNITS:
        msg = f"unknown unit {symbol!r}; use one of {', '.join(UNITS)} or a composed A/B of them"
        raise UnitError(msg)
    return UNITS[symbol]


def _compose(numerator: list[str], denominator: list[str]) -> str:
    numerator, denominator = list(numerator), list(denominator)
    for symbol in list(denominator):
        if symbol in numerator:
            numerator.remove(symbol)
            denominator.remove(symbol)
    head = "*".join(numerator) or "ratio"
    return f"{head}/{'*'.join(denominator)}" if denominator else head


def _dimension_name(dim: Dim) -> str:
    named = DIMENSION_NAMES.get(dim)
    if named is not None:
        return named
    positive = [_power(name, e) for name, e in zip(BASE, dim, strict=True) if e > 0]
    negative = [_power(name, -e) for name, e in zip(BASE, dim, strict=True) if e < 0]
    head = "*".join(positive) or "1"
    if not negative:
        return head
    tail = "*".join(negative)
    return f"{head}/({tail})" if len(negative) > 1 else f"{head}/{tail}"


def _power(name: str, exponent: int) -> str:
    return name if exponent == 1 else f"{name}^{exponent}"
