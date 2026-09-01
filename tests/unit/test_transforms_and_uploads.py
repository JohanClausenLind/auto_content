from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from content_factory.artifacts import FilesystemArtifactStore
from content_factory.data.transforms import (
    DataTransformation,
    DeriveShare,
    FilterEquals,
    SelectColumns,
    SortBy,
    TransformError,
    apply_transformation,
)
from content_factory.ingest.uploads import UploadRejectedError, ingest_upload
from content_factory.schemas.render import DatasetTable

WS = "ws_demo00000001"

RAW = DatasetTable(
    dataset_id="ds_gen000000001",
    classification="SOURCE_DATA",
    columns=("year", "wind_twh", "total_twh"),
    rows=(
        {"year": "2018", "wind_twh": 16.6, "total_twh": 158.0},
        {"year": "2021", "wind_twh": 27.1, "total_twh": 165.0},
        {"year": "2025", "wind_twh": 34.9, "total_twh": 166.0},
    ),
    unit="TWh",
    source_ids=("src_energimynd01",),
)

SPEC = DataTransformation(
    transformation_id="tx_wind00000001",
    input_dataset_id="ds_gen000000001",
    output_dataset_id="ds_share00000001",
    ops=(
        DeriveShare(part="wind_twh", whole="total_twh", out="share_pct", digits=1),
        SelectColumns(columns=("year", "share_pct")),
        SortBy(column="year"),
    ),
    output_unit="%",
    output_label="Wind share of generation",
)


def test_derived_data_reproduces_exactly() -> None:
    a = apply_transformation(RAW, SPEC)
    b = apply_transformation(RAW, SPEC)
    assert a.content_hash() == b.content_hash()
    assert a.classification == "DERIVED_DATA"
    assert a.rows[-1] == {"year": "2025", "share_pct": 21.0}
    assert a.source_ids == RAW.source_ids  # lineage carried


def test_transforms_fail_closed() -> None:
    with pytest.raises(TransformError, match="unknown column"):
        apply_transformation(
            RAW, SPEC.model_copy(update={"ops": (FilterEquals(column="nope", value=1),)})
        )
    with pytest.raises(TransformError, match="empty"):
        apply_transformation(
            RAW, SPEC.model_copy(update={"ops": (FilterEquals(column="year", value="1999"),)})
        )
    with pytest.raises(TransformError, match="does not match"):
        apply_transformation(RAW.model_copy(update={"dataset_id": "ds_other00000001"}), SPEC)


def test_uploads_validate_by_magic_not_extension(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    png = tmp_path / "photo.png"
    Image.new("RGB", (10, 10), (200, 10, 10)).save(png)
    ok = ingest_upload(store, WS, png)
    assert ok.sniffed_mime == "image/png" and ok.kind == "image"
    assert store.exists(WS, ok.artifact.key)
    again = ingest_upload(store, WS, png)
    assert again.artifact.key == ok.artifact.key  # immutable, content-addressed

    fake = tmp_path / "movie.mp4"  # HTML pretending to be a video
    fake.write_text("<html><script>alert(1)</script></html>")
    with pytest.raises(UploadRejectedError, match="not accepted"):
        ingest_upload(store, WS, fake)

    svgish = tmp_path / "logo.svg"
    svgish.write_text('{"not": "svg"}')
    with pytest.raises(UploadRejectedError, match="never accepted"):
        ingest_upload(store, WS, svgish)

    empty = tmp_path / "empty.wav"
    empty.touch()
    with pytest.raises(UploadRejectedError, match="empty"):
        ingest_upload(store, WS, empty)

    data = tmp_path / "table.csv"
    data.write_text("year,share\n2025,21\n")
    ing = ingest_upload(store, WS, data)
    assert ing.kind == "data"
