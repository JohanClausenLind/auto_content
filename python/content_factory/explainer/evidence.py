"""Evidence checks: every number the episode speaks or shows traces to a claim, and adds up."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from content_factory.explainer import units
from content_factory.explainer.errors import ContractIssue
from content_factory.explainer.units import UnitError, unit_for_word
from content_factory.schemas.explainer import (
    AnnotateAction,
    Calculation,
    Claim,
    ClaimOperand,
    DatasetColumn,
    DatasetRow,
    EvidenceDataset,
    EvidencePack,
    Operand,
    Quantity,
    ScriptPlan,
    TextTemplate,
    VisualSpec,
    tokenize,
)

_OPS: dict[str, Callable[[Quantity, Quantity], Quantity]] = {
    "add": units.add,
    "sub": units.sub,
    "mul": units.mul,
    "div": units.div,
}
_NUMBER = re.compile(
    r"^[(\"']*(?P<sign>-?)(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?"
    r"(?P<suffix>%|×|[A-Za-z]+)?[.,;:!?)\"']*$"  # noqa: RUF001  typed "2 times" uses the sign
)
_EPSILON = 1e-9


@dataclass(frozen=True)
class NumberMention:
    """A number as it appears in text, with the unit attached to it or spoken right after it."""

    token_index: int
    raw: str
    value: float
    unit: str | None


def evaluate_calculation(pack: EvidencePack, calc: Calculation) -> Quantity:
    """Recompute a calculation from the pack's claim values, left to right."""
    values = [_operand_value(pack, calc, operand) for operand in calc.operands]
    result = values[0]
    for value in values[1:]:
        result = _OPS[calc.op](result, value)
    return result


def check_calculations(pack: EvidencePack) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for calc in pack.calculations:
        where = f"calculation {calc.calc_id}"
        ids = (calc.calc_id, calc.result_claim_id)
        try:
            computed = evaluate_calculation(pack, calc)
        except UnitError as exc:
            fix = "convert the operands to one dimension or fix the claim unit."
            issues.append(ContractIssue("unit", where, f"{exc}.", fix, ids))
            continue
        except ValueError as exc:
            fix = "fix that operand's value or drop it from the calculation."
            issues.append(ContractIssue("invalid_value", where, f"{exc}.", fix, ids))
            continue
        claimed = pack.claim(calc.result_claim_id).value
        if claimed is None:
            message = f"result claim {calc.result_claim_id} has no value."
            issues.append(ContractIssue("invalid_value", where, message, "give it one.", ids))
            continue
        try:
            in_unit = units.convert(computed, claimed.unit)
        except UnitError as exc:
            fix = f"change the claim unit to {computed.unit} or fix the inputs."
            issues.append(ContractIssue("unit", where, f"{exc}.", fix, ids))
            continue
        if _within_uncertainty(claimed, in_unit.magnitude):
            continue
        operands = ", ".join(_describe_operand(pack, operand) for operand in calc.operands)
        message = (
            f"{calc.op}({operands}) = {_fmt(in_unit)}, but claim {calc.result_claim_id} "
            f"says {_fmt(claimed)}."
        )
        fix = f"change the claim value to {_fmt(in_unit)} or fix the inputs."
        issues.append(ContractIssue("arithmetic", where, message, fix, ids))
    return issues


def check_datasets(pack: EvidencePack) -> list[ContractIssue]:
    """Numbers are never invented to fill a chart: every quantitative cell is a claim value."""
    issues: list[ContractIssue] = []
    for dataset in pack.datasets:
        for row in dataset.rows:
            cells = _numeric_cells(dataset, row)
            if not cells:
                continue
            where = f"dataset {dataset.dataset_id} row {row.key!r}"
            ids = (dataset.dataset_id, row.key)
            claims = [c for c in _claims(pack, row.claim_ids)[0] if c.value is not None]
            if not row.claim_ids:
                message = "the row has quantitative cells but no claim_ids."
                fix = "add the claim ids the cells come from; numbers are never invented."
                issues.append(ContractIssue("dataset_mismatch", where, message, fix, ids))
                continue
            for column, cell in cells:
                spot = f"{where} column {column.name}"
                issues.extend(_cell_issues(spot, cell, column.unit, claims, ids))
    return issues


def number_mentions(tokens: tuple[str, ...]) -> list[NumberMention]:
    """Every numeric token with its unit; number words such as "twice" are not parsed."""
    mentions: list[NumberMention] = []
    for index, token in enumerate(tokens):
        match = _NUMBER.match(token)
        if match is None:
            continue
        value = float(match["sign"] + match["int"].replace(",", "") + (match["frac"] or ""))
        suffix = match["suffix"]
        if suffix is not None:
            unit = units.UNIT_WORDS.get(suffix)
            if unit is None or (suffix == "s" and _looks_like_year(value)):
                continue
        else:
            unit = _following_unit(tokens, index)
        mentions.append(NumberMention(index, token, value, unit))
    return mentions


def check_narration(pack: EvidencePack, script: ScriptPlan) -> list[ContractIssue]:
    """Every number spoken or displayed in a segment is one of that segment's claim values."""
    issues: list[ContractIssue] = []
    for segment in script.segments:
        where = f"segment {segment.segment_id}"
        ids = (segment.segment_id,)
        claims, unknown = _claims(pack, segment.claim_ids)
        issues.extend(_unknown_claim_issues(where, unknown, ids))
        issues.extend(_mention_issues(where, segment.tokens, claims, ids, "narration"))
        if segment.display_text:
            tokens = tokenize(segment.display_text)
            issues.extend(_mention_issues(f"{where} display_text", tokens, claims, ids, "text"))
    return issues


def check_spec_text_numbers(pack: EvidencePack, spec: VisualSpec) -> list[ContractIssue]:
    """Text items and annotations bound to a claim say that claim's value and nothing else."""
    issues: list[ContractIssue] = []
    for scene in spec.scenes:
        where = f"scene {scene.scene_id}"
        if isinstance(scene.template, TextTemplate):
            for item in scene.template.items:
                if item.claim_id is not None:
                    spot = f"{where} item {item.entity_id}"
                    ids = (scene.scene_id, item.entity_id)
                    issues.extend(_text_issues(pack, spot, item.text, item.claim_id, ids))
        for beat in scene.beats:
            for action in beat.actions:
                if isinstance(action, AnnotateAction) and action.claim_id is not None:
                    spot = f"{where} beat {beat.beat_id} annotate"
                    ids = (scene.scene_id, beat.beat_id)
                    issues.extend(_text_issues(pack, spot, action.text, action.claim_id, ids))
    return issues


def check_evidence(
    pack: EvidencePack, script: ScriptPlan | None = None, spec: VisualSpec | None = None
) -> list[ContractIssue]:
    issues = check_calculations(pack) + check_datasets(pack)
    if script is not None:
        issues += check_narration(pack, script)
    if spec is not None:
        issues += check_spec_text_numbers(pack, spec)
    return issues


def _operand_value(pack: EvidencePack, calc: Calculation, operand: Operand) -> Quantity:
    if not isinstance(operand, ClaimOperand):
        return operand.value
    value = pack.claim(operand.claim_id).value
    if value is None:
        msg = f"calculation {calc.calc_id}: claim {operand.claim_id} has no value"
        raise ValueError(msg)
    return value


def _describe_operand(pack: EvidencePack, operand: Operand) -> str:
    if isinstance(operand, ClaimOperand):
        value = pack.claim(operand.claim_id).value
        shown = "no value" if value is None else _fmt(value)
        return f"{shown} ({operand.claim_id})"
    return f"{_fmt(operand.value)} (constant)"


def _numeric_cells(dataset: EvidenceDataset, row: DatasetRow) -> list[tuple[DatasetColumn, float]]:
    cells: list[tuple[DatasetColumn, float]] = []
    for column, cell in zip(dataset.columns, row.values, strict=True):
        if column.kind == "quantitative" and isinstance(cell, float):
            cells.append((column, cell))
    return cells


def _within_uncertainty(claimed: Quantity, value: float) -> bool:
    if claimed.uncertainty == "interval":
        assert claimed.low is not None and claimed.high is not None
        return claimed.low <= value <= claimed.high
    if claimed.uncertainty == "estimate":
        tolerance = 0.05 * abs(claimed.magnitude)
    elif claimed.uncertainty == "rounded":
        tolerance = 0.5 * 10.0 ** -_decimals(claimed.magnitude)
    else:
        tolerance = _EPSILON * abs(claimed.magnitude)
    return abs(value - claimed.magnitude) <= max(tolerance, _EPSILON)


def _cell_issues(
    where: str, cell: float, unit: str, claims: Sequence[Claim], ids: tuple[str, ...]
) -> list[ContractIssue]:
    comparable: list[tuple[float, str]] = []
    conflicts: list[str] = []
    for claim in claims:
        value = claim.value
        assert value is not None
        if not unit:
            comparable.append((value.magnitude, claim.claim_id))
        elif units.same_dimension(value.unit, unit):
            comparable.append((units.magnitude_in(value, unit), claim.claim_id))
        else:
            conflicts.append(f"{_fmt(value)} ({claim.claim_id})")
    for candidate, _ in comparable:
        if abs(cell - candidate) <= max(_EPSILON * abs(candidate), _EPSILON):
            return []
    shown = f"{_fmt_number(cell)} {unit}".rstrip()
    if not comparable and conflicts:
        message = (
            f"cell {shown} is {units.dimension_of(unit)}; the row's claims are "
            f"{', '.join(conflicts)}."
        )
        fix = "change the column unit or reference a claim in that dimension."
        return [ContractIssue("unit", where, message, fix, ids)]
    if comparable:
        nearest, claim_id = min(comparable, key=lambda pair: abs(cell - pair[0]))
        nearest_text = f"nearest claim value is {_fmt_number(nearest)} {unit} ({claim_id})".rstrip()
    else:
        nearest_text = "none of the row's claims carries a value"
    message = f"cell {shown} matches no claim in the row; {nearest_text}."
    fix = "set the cell to a referenced claim's value or reference the claim it comes from."
    return [ContractIssue("dataset_mismatch", where, message, fix, ids)]


def _text_issues(
    pack: EvidencePack, where: str, text: str, claim_id: str, ids: tuple[str, ...]
) -> list[ContractIssue]:
    claims, unknown = _claims(pack, (claim_id,))
    if unknown:
        return _unknown_claim_issues(where, unknown, ids)
    return _mention_issues(where, tokenize(text), claims, ids, "text")


def _mention_issues(
    where: str, tokens: tuple[str, ...], claims: Sequence[Claim], ids: tuple[str, ...], noun: str
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    valued = [c for c in claims if c.value is not None]
    for mention in number_mentions(tokens):
        if _is_year(mention, claims):
            continue
        # (distance, "value unit", claim id) per comparable claim; the nearest one names the fix.
        said: list[tuple[float, str, str]] = []
        conflicts: list[str] = []
        tolerance = _tolerance(mention, tokens)
        for claim in valued:
            value = claim.value
            assert value is not None
            if mention.unit is None:
                candidate, unit = value.magnitude, value.unit
            elif units.same_dimension(value.unit, mention.unit):
                candidate, unit = units.magnitude_in(value, mention.unit), mention.unit
            else:
                conflicts.append(f"{_fmt(value)} ({claim.claim_id})")
                continue
            distance = abs(mention.value - candidate)
            said.append((distance, f"{_fmt_number(candidate)} {unit}", claim.claim_id))
        if any(distance <= tolerance for distance, _, _ in said):
            continue
        spot = f"{where} token {mention.token_index}"
        if not said and conflicts and mention.unit is not None:
            message = (
                f"the {noun} says {mention.raw!r} ({units.dimension_of(mention.unit)}) but the "
                f"referenced claims say {', '.join(conflicts)}."
            )
            fix = "change the unit in the text or reference a claim in that dimension."
            issues.append(ContractIssue("unit", spot, message, fix, ids))
            continue
        values = ", ".join(f"{shown} ({cid})" for _, shown, cid in said) or "nothing with a value"
        message = f"the {noun} says {mention.raw!r} but the referenced claims say {values}."
        wanted = min(said)[1] if said else "a claim value"
        spoken = f"{_fmt_number(mention.value)} {mention.unit or ''}".rstrip()
        fix = f"make the {noun} say {wanted} or reference the claim that says {spoken}."
        issues.append(ContractIssue("narration_mismatch", spot, message, fix, ids))
    return issues


def _is_year(mention: NumberMention, claims: Sequence[Claim]) -> bool:
    if mention.unit is not None or not _looks_like_year(mention.value):
        return False
    return str(int(mention.value)) in {claim.time_basis for claim in claims}


def _looks_like_year(value: float) -> bool:
    return value.is_integer() and 1900 <= value <= 2100


def _following_unit(tokens: tuple[str, ...], index: int) -> str | None:
    following = tokens[index + 1 : index + 3]
    unit, width = None, 0
    if len(following) == 2:
        unit = unit_for_word(" ".join(following))
        width = 2 if unit is not None else 0
    if unit is None and following:
        unit = unit_for_word(following[0])
        width = 1 if unit is not None else 0
    if unit is None:
        return None
    # Spoken compound units: "kilometres per second" is km/s, not km.
    rest = tokens[index + 1 + width : index + 3 + width]
    if len(rest) == 2 and rest[0].strip(",.;:").lower() in {"per", "a", "an", "each", "every"}:
        denominator = unit_for_word(rest[1])
        if denominator is not None:
            return f"{unit}/{denominator}"
    return unit


HEDGES = frozenset({"about", "around", "roughly", "nearly", "almost", "approximately", "some"})


def _tolerance(mention: NumberMention, tokens: tuple[str, ...]) -> float:
    """Half a unit of the stated precision; after a hedge like "about", trailing zeros round."""
    precision = 10.0 ** -_mention_decimals(mention)
    previous = tokens[mention.token_index - 1].strip(",.;:").lower() if mention.token_index else ""
    if previous in HEDGES and _mention_decimals(mention) == 0 and mention.value:
        digits = str(int(abs(mention.value)))
        precision = 10.0 ** (len(digits) - len(digits.rstrip("0")))
    return 0.5 * precision + _EPSILON


def _mention_decimals(mention: NumberMention) -> int:
    match = _NUMBER.match(mention.raw)
    frac = match["frac"] if match else None
    return len(frac) - 1 if frac else 0


def _decimals(value: float) -> int:
    if value.is_integer():
        return 0
    exponent = Decimal(repr(value)).as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _claims(pack: EvidencePack, claim_ids: Sequence[str]) -> tuple[list[Claim], list[str]]:
    known = {claim.claim_id: claim for claim in pack.claims}
    found = [known[cid] for cid in claim_ids if cid in known]
    return found, [cid for cid in claim_ids if cid not in known]


def _unknown_claim_issues(
    where: str, unknown: Sequence[str], ids: tuple[str, ...]
) -> list[ContractIssue]:
    return [
        ContractIssue(
            "invalid_reference",
            where,
            f"references unknown claim {cid}.",
            "point claim_ids at claims in the evidence pack.",
            (*ids, cid),
        )
        for cid in unknown
    ]


def _fmt(q: Quantity) -> str:
    return f"{_fmt_number(q.magnitude)} {q.unit}"


def _fmt_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.12g}"
