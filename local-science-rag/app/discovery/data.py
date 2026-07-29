"""Validated experimental data formats and immutable local imports."""

from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.database.sqlite import Database


class FermentationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["fermentation"] = "fermentation"
    sample_id: str = Field(min_length=1, max_length=100)
    replicate: int = Field(ge=1)
    time_hours: float = Field(ge=0)
    temperature_c: float = Field(ge=-10, le=80)
    density_g_ml: float = Field(gt=0, le=2)


class VolatileMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["volatiles"] = "volatiles"
    sample_id: str = Field(min_length=1, max_length=100)
    replicate: int = Field(ge=1)
    compound: str = Field(min_length=1, max_length=200)
    concentration: float = Field(ge=0)
    unit: Literal["mg/L", "ug/L"]


class PolyphenolMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["polyphenols"] = "polyphenols"
    sample_id: str = Field(min_length=1, max_length=100)
    replicate: int = Field(ge=1)
    analyte: str = Field(min_length=1, max_length=200)
    concentration_mg_l: float = Field(ge=0)


class SensoryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["sensory"] = "sensory"
    sample_id: str = Field(min_length=1, max_length=100)
    assessor_pseudonym: str = Field(min_length=3, max_length=100)
    replicate: int = Field(ge=1)
    attribute: str = Field(min_length=1, max_length=200)
    score: float
    scale_min: float
    scale_max: float

    def model_post_init(self, __context: object) -> None:
        if self.scale_max <= self.scale_min or not self.scale_min <= self.score <= self.scale_max:
            raise ValueError("sensory score must remain inside a non-empty declared scale")


ExperimentalRecord = Annotated[
    FermentationPoint | VolatileMeasurement | PolyphenolMeasurement | SensoryObservation,
    Field(discriminator="kind"),
]


class ExperimentalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["fermentation", "volatiles", "polyphenols", "sensory"]
    controls: dict[str, str] = Field(min_length=1, max_length=30)
    records: list[ExperimentalRecord] = Field(min_length=1, max_length=1_000_000)

    def model_post_init(self, __context: object) -> None:
        if any(record.kind != self.kind for record in self.records):
            raise ValueError("every experimental record must match the dataset kind")


class DatasetTransformation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=100)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentalDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: UUID
    kind: Literal["fermentation", "volatiles", "polyphenols", "sensory"]
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stored_path: str
    source_reference: str = Field(min_length=3, max_length=500)
    imported_by: str = Field(min_length=3, max_length=200)
    transformations: list[DatasetTransformation]
    record_count: int = Field(ge=1)
    controls: dict[str, str]
    imported_at: datetime


def load_experimental_dataset(path: str | Path) -> ExperimentalDataset:
    source = Path(path)
    if source.suffix.lower() == ".json":
        return ExperimentalDataset.model_validate_json(source.read_text(encoding="utf-8"))
    if source.suffix.lower() != ".csv":
        raise ValueError("experimental datasets must be UTF-8 JSON or CSV")
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    if not rows or "kind" not in rows[0]:
        raise ValueError("CSV requires a kind column and at least one record")
    kind = rows[0]["kind"]
    record_model = {
        "fermentation": FermentationPoint,
        "volatiles": VolatileMeasurement,
        "polyphenols": PolyphenolMeasurement,
        "sensory": SensoryObservation,
    }.get(kind)
    if record_model is None:
        raise ValueError("CSV kind is unsupported")
    control_columns = [
        column for column in reader.fieldnames or [] if column.startswith("control_")
    ]
    controls = {
        column.removeprefix("control_"): rows[0][column]
        for column in control_columns
        if rows[0].get(column)
    }
    records = [
        record_model.model_validate(
            {key: value for key, value in row.items() if key not in control_columns}
        )
        for row in rows
    ]
    return ExperimentalDataset(kind=kind, controls=controls, records=records)


class ExperimentalDatasetImporter:
    def __init__(self, database_path: str | Path, storage_root: str | Path) -> None:
        self.database = Database(database_path)
        self.storage_root = Path(storage_root)

    def import_file(
        self,
        path: str | Path,
        *,
        source_reference: str,
        imported_by: str,
        transformations: list[DatasetTransformation] | None = None,
    ) -> ExperimentalDatasetManifest:
        source = Path(path)
        raw_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        dataset = load_experimental_dataset(source)
        destination = self.storage_root / raw_sha256 / f"raw{source.suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (
            destination.exists()
            and hashlib.sha256(destination.read_bytes()).hexdigest() != raw_sha256
        ):
            raise RuntimeError("immutable experimental raw-file collision")
        if not destination.exists():
            shutil.copyfile(source, destination)
        manifest = ExperimentalDatasetManifest(
            id=uuid4(),
            kind=dataset.kind,
            raw_sha256=raw_sha256,
            stored_path=str(destination),
            source_reference=source_reference,
            imported_by=imported_by,
            transformations=transformations or [],
            record_count=len(dataset.records),
            controls=dataset.controls,
            imported_at=datetime.now(UTC),
        )
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO experimental_dataset_manifests(
                    id, kind, raw_sha256, manifest_json, imported_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(manifest.id),
                    manifest.kind,
                    manifest.raw_sha256,
                    manifest.model_dump_json(),
                    manifest.imported_by,
                    manifest.imported_at.isoformat(),
                ),
            )
        return manifest
