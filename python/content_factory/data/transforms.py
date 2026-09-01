"""Declarative, reproducible dataset transforms (section 12): classified outputs with executable
transformation records. Same input + same spec = byte-identical output (content hash)."""

from __future__ import annotations

from typing import Annotated, Literal

import polars as pl
from pydantic import Field

from content_factory.schemas.base import OpaqueId, SchemaModel
from content_factory.schemas.render import DatasetTable


class TransformError(Exception):
    pass


class FilterEquals(SchemaModel):
    op: Literal["filter_equals"] = "filter_equals"
    column: str
    value: str | int | float


class SelectColumns(SchemaModel):
    op: Literal["select_columns"] = "select_columns"
    columns: tuple[str, ...] = Field(min_length=1)


class DeriveShare(SchemaModel):
    """share_pct = part / whole * 100, rounded to `digits` (deterministic banker's-free rounding)."""  # noqa: E501

    op: Literal["derive_share"] = "derive_share"
    part: str
    whole: str
    out: str = "share_pct"
    digits: int = Field(default=1, ge=0, le=6)


class SortBy(SchemaModel):
    op: Literal["sort_by"] = "sort_by"
    column: str
    descending: bool = False


TransformOp = Annotated[
    FilterEquals | SelectColumns | DeriveShare | SortBy, Field(discriminator="op")
]


class DataTransformation(SchemaModel):
    transformation_id: OpaqueId
    input_dataset_id: OpaqueId
    output_dataset_id: OpaqueId
    ops: tuple[TransformOp, ...] = Field(min_length=1)
    output_classification: Literal["DERIVED_DATA", "ESTIMATE", "ILLUSTRATIVE"] = "DERIVED_DATA"
    output_unit: str = ""
    output_label: str = ""


def apply_transformation(dataset: DatasetTable, spec: DataTransformation) -> DatasetTable:
    if dataset.dataset_id != spec.input_dataset_id:
        raise TransformError("transformation input does not match the dataset")
    df = pl.DataFrame(list(dataset.rows), infer_schema_length=None)
    for op in spec.ops:
        if isinstance(op, FilterEquals):
            if op.column not in df.columns:
                raise TransformError(f"unknown column {op.column!r}")
            df = df.filter(pl.col(op.column) == op.value)
        elif isinstance(op, SelectColumns):
            missing = [c for c in op.columns if c not in df.columns]
            if missing:
                raise TransformError(f"unknown columns {missing}")
            df = df.select(list(op.columns))
        elif isinstance(op, DeriveShare):
            for col in (op.part, op.whole):
                if col not in df.columns:
                    raise TransformError(f"unknown column {col!r}")
            df = df.with_columns(
                ((pl.col(op.part) / pl.col(op.whole) * 100).round(op.digits)).alias(op.out)
            )
        elif isinstance(op, SortBy):
            df = df.sort(op.column, descending=op.descending)
    if df.is_empty():
        raise TransformError("transformation produced an empty dataset")
    if any(df[c].is_null().any() for c in df.columns):
        raise TransformError("transformation produced nulls — refusing to fill missing data")
    rows = tuple(df.to_dicts())
    return DatasetTable(
        dataset_id=spec.output_dataset_id,
        classification=spec.output_classification,
        columns=tuple(df.columns),
        rows=rows,
        unit=spec.output_unit or dataset.unit,
        source_ids=dataset.source_ids,
        label=spec.output_label or dataset.label,
    )
