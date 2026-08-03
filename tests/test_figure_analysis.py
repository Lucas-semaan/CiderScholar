from __future__ import annotations

import hashlib
from io import BytesIO

import fitz
from PIL import Image

from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.visual_contracts import (
    ScientificFigureAnalysisRequest,
    ScientificFigureAnalysisResponse,
    VisualModelIdentity,
)
from app.llm.figure_analysis import (
    OllamaFigureAnalysisService,
    attach_figure_evidence,
    figure_references_from_chat_records,
)
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord


class _Gateway:
    def __init__(self, *, relevance: float = 0.95, readability: float = 0.90) -> None:
        self.relevance = relevance
        self.readability = readability
        self.requests: list[ScientificFigureAnalysisRequest] = []
        self.images: list[bytes] = []

    def identity(self) -> VisualModelIdentity:
        return VisualModelIdentity(model_id="visual-test", model_revision="revision-a")

    def analyze(
        self,
        request: ScientificFigureAnalysisRequest,
        *,
        image: bytes,
    ) -> ScientificFigureAnalysisResponse:
        assert "path" not in request.model_dump_json()
        assert hashlib.sha256(image).hexdigest() == request.artifact.image_sha256
        self.requests.append(request)
        self.images.append(image)
        return ScientificFigureAnalysisResponse(
            image_sha256=request.artifact.image_sha256,
            figure_type="graph",
            relevance_score=self.relevance,
            readability_score=self.readability,
            supports_answer=True,
            summary="La série traitée présente une tendance supérieure au témoin.",
            visible_variables=["intensité", "traitement"],
            visible_units=["UA"],
            trends=["La série traitée reste au-dessus du témoin."],
            limitations=["Les valeurs ponctuelles ne sont pas toutes lisibles."],
            model_id="visual-test",
            model_revision="revision-a",
            prompt_version=request.prompt_version,
        )


def _seed_figure(settings) -> tuple[ChatEvidenceRecord, Database]:
    paths = corpus_paths(settings, CorpusScope.COMMON)
    pdf_path = paths.pdf_dir / "figure-study.pdf"
    image = Image.new("RGB", (180, 110), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    source_text = (
        "Les résultats du traitement et du témoin sont comparés dans la figure ci-dessous."
    )
    page.insert_text((50, 60), source_text)
    page.insert_image(fitz.Rect(80, 120, 500, 400), stream=stream.getvalue())
    page.insert_text((80, 430), "Figure 1. Intensité mesurée selon le traitement.")
    document.save(pdf_path)
    document.close()

    database = Database(paths.database_path)
    database.initialize()
    report = IngestionPipeline(
        settings_for_corpus(settings, CorpusScope.COMMON),
        database,
    ).ingest_file(pdf_path)
    assert report.status == "chunks_ready"
    assert report.article_id is not None
    chunk = database.chunks_for_article(report.article_id, limit=1)[0]
    record = ChatEvidenceRecord(
        record_id=f"local:{report.article_id}",
        origin="local_rag",
        evidence_level="full_text",
        scope=CorpusScope.COMMON,
        article_id=report.article_id,
        title="Étude avec figure",
        passages=[
            ChatEvidencePassage(
                evidence_id=f"chunk:{chunk['id']}",
                text=str(chunk["text"]),
                chunk_id=int(chunk["id"]),
                page_start=int(chunk["page_start"]),
                page_end=int(chunk["page_end"]),
            )
        ],
    )
    return record, database


def test_local_figure_gateway_receives_only_crop_bytes_and_versioned_contract(settings) -> None:
    record, database = _seed_figure(settings)
    gateway = _Gateway()
    service = OllamaFigureAnalysisService(settings, gateway=gateway)

    first = service.analyze(
        "Quel effet du traitement la figure montre-t-elle ?",
        figure_references_from_chat_records([record]),
    )
    second = service.analyze(
        "Quel effet du traitement la figure montre-t-elle ?",
        figure_references_from_chat_records([record]),
    )

    assert first.processed_count == 1
    assert len(first.admitted) == 1
    assert len(gateway.requests) == 1
    assert second.admitted[0].analysis_id == first.admitted[0].analysis_id
    request = gateway.requests[0]
    assert request.version == 1
    assert request.prompt_version == "scientific-figure-analysis-v1"
    assert request.artifact.byte_size == len(gateway.images[0])
    stored = database.figure_analysis_citation_source(first.admitted[0].analysis_id)
    assert stored is not None
    assert stored["model_revision"] == "revision-a"

    enriched = attach_figure_evidence([record], first.admitted)
    visual = [item for item in enriched[0].passages if item.evidence_kind == "figure"]
    assert len(visual) == 1
    assert visual[0].figure_label == "Figure 1. Intensité mesurée selon le traitement."


def test_low_relevance_figure_is_persisted_but_not_admitted(settings) -> None:
    record, database = _seed_figure(settings)
    service = OllamaFigureAnalysisService(
        settings,
        gateway=_Gateway(relevance=0.45),
    )

    batch = service.analyze(
        "Quel effet du traitement la figure montre-t-elle ?",
        figure_references_from_chat_records([record]),
    )

    assert batch.processed_count == 1
    assert batch.admitted == []
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, validation_reason FROM figure_analysis_runs"
        ).fetchone()
    assert row["status"] == "rejected"
    assert row["validation_reason"] == "insufficient_relevance_readability_or_support"
