"""Actionable contract errors: what is wrong, where, and the one change that fixes it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

IssueKind = Literal[
    "invalid_reference",
    "unknown_field",
    "invalid_value",
    "conflicting_state",
    "arithmetic",
    "unit",
    "narration_mismatch",
    "dataset_mismatch",
    "stale_binding",
    "unsupported",
    "layout",
    "color",
    "timing",
    "evidence",
]


@dataclass(frozen=True)
class ContractIssue:
    """One specific failure at one path, with the fix spelled out."""

    kind: IssueKind
    where: str
    message: str
    fix: str
    ids: tuple[str, ...] = field(default=())

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.message} Fix: {self.fix}"


class EpisodeInvalidError(ValueError):
    """Raised by the compiler boundary with every issue found, never just the first."""

    def __init__(self, issues: list[ContractIssue]) -> None:
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


def explain_validation_error(error: ValidationError, model: type[BaseModel]) -> list[ContractIssue]:
    """Turn pydantic's error list into issues that name the field, the path and the allowed set."""
    issues: list[ContractIssue] = []
    errors = error.errors()
    locs = [tuple(e["loc"]) for e in errors]
    for item in errors:
        loc = tuple(item["loc"])
        if item["type"] == "too_short" and _has_nested_error(loc, locs):
            # pydantic counts only the valid elements, so a bad element also fails min_length.
            continue
        path = _path(loc)
        if item["type"] == "extra_forbidden":
            allowed = _allowed_fields(model, loc[:-1])
            hint = f"allowed fields: {', '.join(allowed)}" if allowed else "check the schema"
            issues.append(
                ContractIssue(
                    kind="unknown_field",
                    where=f"{model.__name__}.{path}" if path else model.__name__,
                    message=f"unknown field {loc[-1]!r}.",
                    fix=f"remove it or fix the spelling; {hint}.",
                    ids=(str(loc[-1]),),
                )
            )
            continue
        message = item["msg"]
        kind: IssueKind = "invalid_reference" if "unknown" in message else "invalid_value"
        issues.append(
            ContractIssue(
                kind=kind,
                where=f"{model.__name__}.{path}" if path else model.__name__,
                message=message.rstrip(".") + ".",
                fix=_fix_for(item, message),
            )
        )
    return issues


def _has_nested_error(loc: tuple[Any, ...], locs: list[tuple[Any, ...]]) -> bool:
    return any(len(other) > len(loc) and other[: len(loc)] == loc for other in locs)


def _fix_for(item: ErrorDetails, message: str) -> str:
    if item["type"] == "missing":
        return f"add the required field {item['loc'][-1]!r}."
    if "references unknown" in message:
        return "point the reference at an existing id or add the missing record."
    if item["type"].startswith("literal") or item["type"] == "enum":
        return "use one of the listed values."
    if item["type"] in {"union_tag_invalid", "union_tag_not_found"}:
        return "set the discriminator field to a supported value."
    return "change the value so the constraint in the message holds."


def _path(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for piece in loc:
        if isinstance(piece, int):
            parts.append(f"[{piece}]")
        elif parts:
            parts.append(f".{piece}")
        else:
            parts.append(str(piece))
    return "".join(parts)


def _allowed_fields(model: type[BaseModel], loc: tuple[Any, ...]) -> list[str]:
    """Walk the model's annotations along a pydantic loc; discriminator tags are skipped."""
    current: Any = model
    for piece in loc:
        if isinstance(piece, int):
            current = _element_type(current)
            continue
        if not (isinstance(current, type) and issubclass(current, BaseModel)):
            return []
        info = current.model_fields.get(str(piece))
        if info is None:
            # A union branch tag such as "chart" or "reveal" is a pydantic loc piece, not a field.
            current = _branch_named(current, str(piece))
            if current is None:
                return []
            continue
        current = info.annotation
    current = _element_type(current)
    if isinstance(current, type) and issubclass(current, BaseModel):
        return sorted(current.model_fields)
    return []


def _element_type(annotation: Any) -> Any:
    args = getattr(annotation, "__args__", ())
    for arg in args:
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
        nested = _element_type(arg)
        if isinstance(nested, type) and issubclass(nested, BaseModel):
            return nested
    return annotation


def _branch_named(annotation: Any, tag: str) -> Any:
    for arg in getattr(annotation, "__args__", ()):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            for info in arg.model_fields.values():
                literal_args = getattr(info.annotation, "__args__", ())
                if tag in literal_args:
                    return arg
        nested = _branch_named(arg, tag)
        if nested is not None:
            return nested
    return None
