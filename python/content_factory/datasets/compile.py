"""Uploaded CSV/JSON to a typed ``DatasetTable``, through transforms that are declared.

Every on-screen figure in this repo resolves through a ``DatasetTable`` to a source, which is the
whole reason `BigNumberScene` takes a `DataRef` and not a number. The stage that was supposed to
produce those tables returned `sample_dataset()` — the committed fixture — so a data-led film
could only ever be about Swedish wind power, and the chain from a spreadsheet an operator actually
has to a chart on screen did not exist.

Two rules make the difference between this and "parse a CSV":

**Transforms are declared, not written.** A transform is a typed step out of a closed set —
select, rename, filter, sort, head, derive-share, round — and the list of them is recorded on the
compiled table's ``label`` and in ``datasets/<id>.transforms.json``. Nothing here executes operator
text, and the derivation is reproducible from the record without reading this module. Polars does
the work (it is already a pinned dependency) because a CSV with mixed types, thousands separators
and a European decimal comma is exactly what it exists for.

**The classification is inferred, never assumed.** A table read straight from an upload is
``SOURCE_DATA``; the moment a transform computes a value that was not in the file it becomes
``DERIVED_DATA``. That distinction is load-bearing downstream: `ChartScene` labels an
``ESTIMATE``/``ILLUSTRATIVE`` table on screen, and calling a derived share "source data" would put
a computed number on a chart claiming to be measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel, sha256_hex
from content_factory.schemas.render import DatasetTable

MAX_ROWS = 5000
"""A table is for a chart, a ranking or a table scene, all of which show at most a couple of dozen
rows. The cap is here so a 200 MB export cannot be loaded into a run's memory by accident, and it
is refused by name rather than silently truncated."""

MAX_COLUMNS = 64

TRANSFORMS_SUFFIX = ".transforms.json"
"""A sidecar declaring how to shape the upload it sits beside — ``energy.csv.transforms.json``.

Named here because two stages have to agree about it: ``ingest`` records it as metadata rather than
as a source, and ``compile_datasets`` must not compile it as a table of its own. It is JSON, so
without this it looked exactly like a data upload and turned one CSV into two datasets."""


def is_transform_sidecar(path: Path) -> bool:
    return path.name.endswith(TRANSFORMS_SUFFIX)


def sidecar_for(path: Path) -> Path:
    return path.with_name(path.name + TRANSFORMS_SUFFIX)


class DatasetError(ValueError):
    """The upload cannot become a table. The message always names the file and the reason."""


TransformKind = Literal["select", "rename", "filter", "sort", "head", "share_of_total", "round"]


class Transform(SchemaModel):
    """One declared step. A closed set on purpose: an operator describes the derivation, and this
    module is the only thing that decides what a description does."""

    kind: TransformKind
    columns: tuple[str, ...] = ()
    """``select``: which columns to keep, in order. ``sort``: the single key. ``round``: which
    numeric columns."""
    mapping: dict[str, str] = Field(default_factory=dict)
    """``rename``: old name -> new name."""
    column: str = ""
    """``filter``/``share_of_total``: the column the step is about."""
    op: Literal["", "eq", "ne", "gt", "gte", "lt", "lte", "contains"] = ""
    value: str | float | int | None = None
    descending: bool = False
    limit: int | None = Field(default=None, ge=1, le=MAX_ROWS)
    digits: int = Field(default=1, ge=0, le=6)
    into: str = ""
    """``share_of_total``: the new column's name. Naming it is required, because an unnamed derived
    column is a number on a chart nobody can trace."""

    @model_validator(mode="after")
    def _coherent(self) -> Transform:
        need_columns = {"select", "sort", "round"}
        if self.kind in need_columns and not self.columns:
            msg = f"{self.kind} needs `columns`"
            raise ValueError(msg)
        if self.kind == "rename" and not self.mapping:
            msg = "rename needs `mapping`"
            raise ValueError(msg)
        if self.kind == "filter" and not (self.column and self.op):
            msg = "filter needs `column` and `op`"
            raise ValueError(msg)
        if self.kind == "head" and self.limit is None:
            msg = "head needs `limit`"
            raise ValueError(msg)
        if self.kind == "share_of_total" and not (self.column and self.into):
            msg = (
                "share_of_total needs `column` and `into`"
                " (an unnamed derived column is untraceable)"
            )
            raise ValueError(msg)
        if self.kind == "sort" and len(self.columns) != 1:
            msg = "sort takes exactly one column"
            raise ValueError(msg)
        return self

    def describe(self) -> str:
        """One line, readable, for the table's own label. This is what a viewer's source note and
        an operator's audit both read; it must say what happened without this module."""
        if self.kind == "select":
            return f"select {', '.join(self.columns)}"
        if self.kind == "rename":
            return "rename " + ", ".join(f"{a}->{b}" for a, b in sorted(self.mapping.items()))
        if self.kind == "filter":
            return f"filter {self.column} {self.op} {self.value!r}"
        if self.kind == "sort":
            return f"sort by {self.columns[0]}" + (" desc" if self.descending else " asc")
        if self.kind == "head":
            return f"first {self.limit} rows"
        if self.kind == "share_of_total":
            return f"{self.into} = {self.column} / sum({self.column})"
        return f"round {', '.join(self.columns)} to {self.digits}dp"

    @property
    def derives(self) -> bool:
        """Whether this step puts a number in the table that was not in the file."""
        return self.kind == "share_of_total"


@dataclass(frozen=True)
class CompiledDataset:
    table: DatasetTable
    transforms: tuple[Transform, ...]
    source_path: Path
    input_sha256: str

    def record(self) -> dict[str, Any]:
        """What is written beside the table so the derivation is reproducible from disk."""
        return {
            "dataset_id": self.table.dataset_id,
            "source_file": self.source_path.name,
            "input_sha256": self.input_sha256,
            "classification": self.table.classification,
            "transforms": [t.model_dump(mode="json") for t in self.transforms],
            "described": [t.describe() for t in self.transforms],
            "columns": list(self.table.columns),
            "rows": len(self.table.rows),
        }


def _read_frame(path: Path):
    """The upload as a polars DataFrame. CSV or JSON; anything else is refused by name."""
    import polars as pl

    suffix = path.suffix.lower()
    if suffix not in (".csv", ".json"):
        # Refused before the bytes are read: an unsupported 200 MB export should not be loaded
        # into memory to be told no.
        msg = (
            f"{path.name}: only .csv and .json uploads become datasets"
            f" (got {suffix or 'no suffix'})"
        )
        raise DatasetError(msg)
    try:
        if suffix == ".csv":
            # A European export writes 1 234,5 with a semicolon separator. Both are tried, in
            # order, because guessing silently wrong turns every number into a string.
            for separator in (",", ";", "\t"):
                frame = pl.read_csv(
                    path, separator=separator, try_parse_dates=False, infer_schema_length=1000
                )
                if frame.width > 1 or separator == "\t":
                    return frame
            return frame
        if suffix == ".json":
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
            if isinstance(payload, dict):
                # A single object, or {"rows": [...]}/{"data": [...]}.
                for key in ("rows", "data", "records", "items"):
                    if isinstance(payload.get(key), list):
                        payload = payload[key]
                        break
                else:
                    payload = [payload]
            if not isinstance(payload, list) or not payload:
                msg = f"{path.name}: JSON must be a non-empty array of objects"
                raise DatasetError(msg)
            return pl.DataFrame(payload)
    except DatasetError:
        raise
    except Exception as exc:
        msg = f"{path.name} cannot be read as a table: {exc}"
        raise DatasetError(msg) from exc
    msg = f"{path.name}: unreachable — the suffix was checked above"  # pragma: no cover
    raise DatasetError(msg)


def _apply(frame, step: Transform):
    import polars as pl

    if step.kind == "select":
        missing = [c for c in step.columns if c not in frame.columns]
        if missing:
            msg = f"select names columns the file does not have: {missing}"
            raise DatasetError(msg)
        return frame.select(list(step.columns))
    if step.kind == "rename":
        missing = [c for c in step.mapping if c not in frame.columns]
        if missing:
            msg = f"rename names columns the file does not have: {missing}"
            raise DatasetError(msg)
        return frame.rename(dict(step.mapping))
    if step.kind == "filter":
        if step.column not in frame.columns:
            msg = f"filter names a column the file does not have: {step.column!r}"
            raise DatasetError(msg)
        col = pl.col(step.column)
        ops = {
            "eq": lambda: col == step.value,
            "ne": lambda: col != step.value,
            "gt": lambda: col > step.value,
            "gte": lambda: col >= step.value,
            "lt": lambda: col < step.value,
            "lte": lambda: col <= step.value,
            "contains": lambda: col.cast(pl.Utf8).str.contains(str(step.value), literal=True),
        }
        return frame.filter(ops[step.op]())
    if step.kind == "sort":
        if step.columns[0] not in frame.columns:
            msg = f"sort names a column the file does not have: {step.columns[0]!r}"
            raise DatasetError(msg)
        return frame.sort(step.columns[0], descending=step.descending)
    if step.kind == "head":
        return frame.head(step.limit)
    if step.kind == "share_of_total":
        if step.column not in frame.columns:
            msg = f"share_of_total names a column the file does not have: {step.column!r}"
            raise DatasetError(msg)
        total = frame.select(pl.col(step.column).cast(pl.Float64).sum()).item()
        if not total:
            msg = f"share_of_total: {step.column} sums to zero, so a share is undefined"
            raise DatasetError(msg)
        return frame.with_columns(
            (pl.col(step.column).cast(pl.Float64) / total * 100.0).alias(step.into)
        )
    missing = [c for c in step.columns if c not in frame.columns]
    if missing:
        msg = f"round names columns the file does not have: {missing}"
        raise DatasetError(msg)
    return frame.with_columns(
        [pl.col(c).cast(pl.Float64).round(step.digits).alias(c) for c in step.columns]
    )


def compile_dataset(
    path: Path,
    *,
    dataset_id: str,
    transforms: tuple[Transform, ...] = (),
    source_ids: tuple[str, ...] = (),
    unit: str = "",
    label: str = "",
) -> CompiledDataset:
    """One upload, one declared transform list, one typed table.

    Pure with respect to the file: the same bytes and the same transforms give the same table, and
    ``input_sha256`` is over the file so a re-uploaded spreadsheet is a different dataset.
    """
    frame = _read_frame(path)
    raw = path.read_bytes()
    if frame.height == 0:
        msg = f"{path.name} has no rows"
        raise DatasetError(msg)
    if frame.width > MAX_COLUMNS:
        msg = f"{path.name} has {frame.width} columns; the limit is {MAX_COLUMNS}"
        raise DatasetError(msg)
    for step in transforms:
        frame = _apply(frame, step)
    if frame.height == 0:
        msg = f"{path.name}: the declared transforms left no rows"
        raise DatasetError(msg)
    if frame.height > MAX_ROWS:
        msg = (
            f"{path.name} reduces to {frame.height} rows; the limit is {MAX_ROWS}."
            " Add a `head` or a `filter` transform — a scene shows a couple of dozen rows"
        )
        raise DatasetError(msg)
    rows = tuple(
        {
            k: (v if isinstance(v, str | int | float) or v is None else str(v))
            for k, v in row.items()
        }
        for row in frame.iter_rows(named=True)
    )
    described = [t.describe() for t in transforms]
    table = DatasetTable(
        dataset_id=dataset_id,
        # SOURCE_DATA until a transform computes something the file did not contain. Getting this
        # wrong would put a derived number on a chart that claims to be measured.
        classification="DERIVED_DATA" if any(t.derives for t in transforms) else "SOURCE_DATA",
        columns=tuple(frame.columns),
        rows=rows,
        unit=unit,
        source_ids=source_ids,
        label=(label or f"{path.name}" + (f" ({'; '.join(described)})" if described else ""))[:200],
    )
    return CompiledDataset(table, tuple(transforms), path, sha256_hex(raw))


def parse_transforms(payload: object) -> tuple[Transform, ...]:
    """A transform list as it arrives from a widget or a sidecar file. Refused by name when wrong,
    because a mistyped transform that validated as an empty list would silently change the film."""
    if payload in (None, "", []):
        return ()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            msg = f"transforms must be a JSON array: {exc}"
            raise DatasetError(msg) from exc
    if not isinstance(payload, list):
        msg = f"transforms must be a JSON array, got {type(payload).__name__}"
        raise DatasetError(msg)
    try:
        return tuple(Transform.model_validate(item) for item in payload)
    except Exception as exc:
        msg = f"transform is not one this module can run: {exc}"
        raise DatasetError(msg) from exc
