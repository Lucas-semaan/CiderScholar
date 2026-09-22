"""Bounded admission of verified JATS/TEI source assets into the common corpus."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.database.sqlite import Database
from app.ingestion.chunker import ScientificChunker
from app.ingestion.deduplication import sha256_file
from app.ingestion.native_xml import JatsXmlExtractor, NativeXmlExtractionError, TeiXmlExtractor

NativeFormat = Literal["jats_xml", "tei_xml"]


class NativeArticleMetadata(BaseModel):
    """Bibliographic metadata already validated before native-text admission."""

    model_config = ConfigDict(extra="forbid")

    doi: str = Field(pattern=r"^10\.\d{4,9}/\S+$")
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=500)
    abstract: str | None = Field(default=None, max_length=50_000)
    journal: str | None = Field(default=None, max_length=500)
    work_type: str | None = Field(default=None, max_length=100)
    publisher: str | None = Field(default=None, max_length=500)
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    language: str | None = Field(default=None, max_length=20)
    source: str = Field(min_length=1, max_length=200)


class NativeAssetInput(BaseModel):
    """Verified local native asset; the caller owns download and access rights."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    format: NativeFormat
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=255)
    byte_count: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2_000)
    license: str | None = Field(default=None, max_length=500)
    native_asset_id: str | None = Field(default=None, max_length=128)


class NativeIngestionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_count: int = Field(ge=0)
    reused_existing_asset: bool
    source_format: NativeFormat


class NativeTextIngestionService:
    """Parse and atomically admit one already-downloaded, source-native asset."""

    def __init__(self, database: Database, chunker: ScientificChunker) -> None:
        self.database = database
        self.chunker = chunker

    @staticmethod
    def _extractor(source_format: NativeFormat) -> JatsXmlExtractor | TeiXmlExtractor:
        if source_format == "jats_xml":
            return JatsXmlExtractor()
        return TeiXmlExtractor()

    def ingest(
        self,
        *,
        metadata: NativeArticleMetadata,
        asset: NativeAssetInput,
    ) -> NativeIngestionReport:
        path = asset.path.resolve()
        if not path.is_file():
            raise NativeXmlExtractionError("native source file is unavailable")
        if sha256_file(path) != asset.sha256:
            raise NativeXmlExtractionError("native source SHA-256 does not match verified asset")
        if path.stat().st_size != asset.byte_count:
            raise NativeXmlExtractionError("native source byte count does not match verified asset")

        document = self._extractor(asset.format).extract(path)
        chunks: list[dict[str, object]] = []
        for block in document.structural_blocks:
            for chunk in self.chunker.chunk_structural_blocks([block]):
                chunks.append(
                    {
                        "section": chunk.section,
                        "text": chunk.text,
                        "token_count": chunk.token_count,
                        "section_path": block.section_path,
                        "paragraph_start": block.paragraph_number,
                        "paragraph_end": block.paragraph_number,
                        "xml_id_start": block.xml_id,
                        "xml_id_end": block.xml_id,
                    }
                )
        if not chunks:
            raise NativeXmlExtractionError("native source produced no indexable structural chunks")

        article_id, chunk_ids, reused = self.database.admit_native_asset_and_chunks(
            article={
                "id": str(uuid.uuid4()),
                "sha256": asset.sha256,
                **metadata.model_dump(mode="python"),
            },
            asset={
                "kind": asset.format,
                "file_path": str(path),
                "sha256": asset.sha256,
                "media_type": asset.media_type,
                "byte_count": asset.byte_count,
                "provider": asset.provider,
                "source_url": asset.source_url,
                "license": asset.license,
                "native_asset_id": asset.native_asset_id,
            },
            chunks=chunks,
            outline_nodes=[node.model_dump(mode="python") for node in document.outline_nodes],
        )
        return NativeIngestionReport(
            article_id=article_id,
            asset_sha256=asset.sha256,
            chunk_count=len(chunk_ids),
            reused_existing_asset=reused,
            source_format=asset.format,
        )
