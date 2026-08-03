"""Local, question-targeted figure analysis through the loopback Ollama API."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import fitz
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.ingestion.visual_contracts import (
    ScientificFigureAnalysisGateway,
    ScientificFigureAnalysisRequest,
    ScientificFigureAnalysisResponse,
    VisualArtifactDescriptor,
    VisualCaptionContext,
    VisualModelIdentity,
)
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord

_TERM = re.compile(r"[\wÀ-ÿ-]{3,}", re.UNICODE)
_FIGURE_ANALYSIS_LOCK = threading.Lock()
_PROMPT_VERSION = "scientific-figure-analysis-v1"


class FigureAnalysisUnavailable(RuntimeError):
    """The explicitly requested local vision engine cannot be used."""


class FigureSourceReference(BaseModel):
    """One retained textual passage from which nearby figures may be considered."""

    model_config = ConfigDict(extra="forbid")

    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    rank: int = Field(default=0, ge=0)


class FigureEvidence(BaseModel):
    """Persisted visual observation admitted for downstream scientific reasoning."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(pattern=r"^figure-analysis-[0-9a-f]{24}$")
    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    element_id: str = Field(min_length=1, max_length=500)
    local_element_id: str = Field(pattern=r"^figure-p[0-9]{4}-[0-9]{3}$")
    related_chunk_id: int = Field(ge=1)
    page_number: int = Field(ge=1)
    figure_label: str = Field(min_length=1, max_length=500)
    observation_text: str = Field(min_length=1, max_length=4_000)
    observation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_name: str = Field(min_length=1, max_length=200)
    relevance_score: float = Field(ge=0.0, le=1.0)
    readability_score: float = Field(ge=0.0, le=1.0)


class FigureAnalysisBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed_count: int = Field(ge=0)
    admitted: list[FigureEvidence] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=10)
    duration_seconds: float = Field(ge=0.0)
    model_name: str


class _VisionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_type: Literal["graph", "diagram", "photo", "map", "microscopy", "other"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    readability_score: float = Field(ge=0.0, le=1.0)
    supports_answer: bool
    summary: str = Field(min_length=1, max_length=1_200)
    visible_variables: list[str] = Field(default_factory=list, max_length=12)
    visible_units: list[str] = Field(default_factory=list, max_length=12)
    trends: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=6)


@dataclass(frozen=True, slots=True)
class _Candidate:
    scope: CorpusScope
    database: Database
    article_id: str
    article_sha256: str
    pdf_path: Path
    element: dict[str, Any]
    related_chunk_id: int
    rank: int
    lexical_score: float

    @property
    def figure_label(self) -> str:
        caption = str(self.element.get("original_caption") or "").strip()
        return caption[:500] if caption else f"Figure, page {self.element['page_number']}"


@dataclass(frozen=True, slots=True)
class _RenderedCrop:
    image: bytes
    width: int
    height: int


def figure_references_from_chat_records(
    records: Sequence[ChatEvidenceRecord],
) -> list[FigureSourceReference]:
    """Translate the final textual selection into bounded local figure references."""

    references: list[FigureSourceReference] = []
    for rank, record in enumerate(records):
        if (
            record.origin != "local_rag"
            or record.evidence_level != "full_text"
            or record.scope is None
            or record.article_id is None
        ):
            continue
        for passage in record.passages:
            if (
                passage.evidence_kind != "text"
                or passage.chunk_id is None
                or passage.page_start is None
                or passage.page_end is None
            ):
                continue
            references.append(
                FigureSourceReference(
                    scope=record.scope,
                    article_id=record.article_id,
                    chunk_id=passage.chunk_id,
                    page_start=passage.page_start,
                    page_end=passage.page_end,
                    rank=rank,
                )
            )
    return references


def attach_figure_evidence(
    records: Sequence[ChatEvidenceRecord],
    figures: Sequence[FigureEvidence],
) -> list[ChatEvidenceRecord]:
    """Attach admitted observations to their existing full-text article records."""

    by_article: dict[tuple[CorpusScope, str], list[FigureEvidence]] = {}
    for figure in figures:
        by_article.setdefault((figure.scope, figure.article_id), []).append(figure)
    enriched: list[ChatEvidenceRecord] = []
    for record in records:
        key = (
            (record.scope, record.article_id)
            if record.scope is not None and record.article_id is not None
            else None
        )
        additions = by_article.get(key, []) if key is not None else []
        passages = list(record.passages)
        for figure in additions:
            passages.append(
                ChatEvidencePassage(
                    evidence_id=f"figure:{figure.analysis_id}",
                    text=figure.observation_text,
                    evidence_kind="figure",
                    chunk_id=figure.related_chunk_id,
                    section="Figure",
                    page_start=figure.page_number,
                    page_end=figure.page_number,
                    figure_analysis_id=figure.analysis_id,
                    figure_label=figure.figure_label,
                    visual_model=figure.model_name,
                    source_image_sha256=figure.image_sha256,
                )
            )
        enriched.append(record.model_copy(update={"passages": passages[:8]}))
    return enriched


class OllamaScientificFigureAnalysisGateway:
    """Loopback-only implementation of the provider-neutral pixel boundary."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = settings.figure_analysis
        self._http = httpx.Client(
            base_url=f"{self.config.base_url}/",
            timeout=httpx.Timeout(self.config.request_timeout_seconds, connect=3.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._identity: VisualModelIdentity | None = None
        self._closed = False

    def identity(self) -> VisualModelIdentity:
        if self._identity is not None:
            return self._identity
        try:
            response = self._http.get("api/tags")
            response.raise_for_status()
            payload = response.json()
            models = [
                item
                for item in payload.get("models", [])
                if isinstance(item, dict)
                and str(item.get("name") or item.get("model") or "") == self.config.model
            ]
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise FigureAnalysisUnavailable(
                "Ollama local est indisponible pour l’analyse des figures."
            ) from error
        if not models:
            raise FigureAnalysisUnavailable(
                f"Le modèle local {self.config.model} n’est pas installé dans Ollama."
            )
        model = models[0]
        revision = str(model.get("digest") or model.get("modified_at") or "ollama-local")
        self._identity = VisualModelIdentity(
            model_id=self.config.model,
            model_revision=revision[:200],
        )
        return self._identity

    def analyze(
        self,
        request: ScientificFigureAnalysisRequest,
        *,
        image: bytes,
    ) -> ScientificFigureAnalysisResponse:
        if self._closed:
            raise RuntimeError("figure analysis gateway is closed")
        if hashlib.sha256(image).hexdigest() != request.artifact.image_sha256:
            raise ValueError("visual artifact bytes do not match their descriptor")
        identity = self.identity()
        prompt = {
            "question_scientifique": request.question,
            "legende_originale": request.context.original_caption,
            "extraits_textuels_voisins": request.context.related_source_excerpts,
            "consigne": (
                "Analyse uniquement ce qui est réellement visible. Évalue la pertinence directe "
                "pour la question et la lisibilité des axes, unités, groupes et tendances. "
                "N’invente aucune valeur, causalité ou conclusion. N’utilise supports_answer=true "
                "que si la figure apporte une observation scientifique directe et très pertinente. "
                "Les tendances doivent rester qualitatives si une valeur exacte est ambiguë."
            ),
        }
        try:
            response = self._http.post(
                "api/chat",
                json={
                    "model": identity.model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(prompt, ensure_ascii=False),
                            "images": [base64.b64encode(image).decode("ascii")],
                        }
                    ],
                    "stream": False,
                    "think": False,
                    "format": _VisionDraft.model_json_schema(),
                    "keep_alive": "5m",
                    "options": {
                        "temperature": 0.0,
                        "num_ctx": 4096,
                        "num_predict": 700,
                    },
                },
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            draft = _VisionDraft.model_validate_json(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as error:
            raise RuntimeError("Ollama returned an invalid figure analysis") from error
        return ScientificFigureAnalysisResponse(
            image_sha256=request.artifact.image_sha256,
            **draft.model_dump(),
            model_id=identity.model_id,
            model_revision=identity.model_revision,
            prompt_version=request.prompt_version,
            warnings=[],
        )

    def close(self) -> None:
        if not self._closed:
            self._http.close()
            self._closed = True


class OllamaFigureAnalysisService:
    """Analyze at most five post-retrieval figures, sequentially and locally."""

    def __init__(
        self,
        settings: Settings,
        *,
        gateway: ScientificFigureAnalysisGateway | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.config = settings.figure_analysis
        self.gateway = gateway or OllamaScientificFigureAnalysisGateway(
            settings,
            transport=transport,
        )
        self._owns_gateway = gateway is None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_gateway:
            close = getattr(self.gateway, "close", None)
            if callable(close):
                close()
        self._closed = True

    def __enter__(self) -> OllamaFigureAnalysisService:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def analyze(
        self,
        question: str,
        references: Sequence[FigureSourceReference],
        *,
        on_analysis_started: Callable[[], None] | None = None,
    ) -> FigureAnalysisBatch:
        started = perf_counter()
        if not self.config.enabled:
            raise FigureAnalysisUnavailable("L’analyse locale des figures est désactivée.")
        candidates = self._candidates(question, references)
        if not candidates:
            return FigureAnalysisBatch(
                processed_count=0,
                admitted=[],
                warnings=["Aucune figure liée aux passages retenus n’a été trouvée."],
                duration_seconds=perf_counter() - started,
                model_name=self.config.model,
            )
        identity = self.gateway.identity()
        if on_analysis_started is not None:
            on_analysis_started()
        admitted: list[FigureEvidence] = []
        warnings: list[str] = []
        processed = 0
        with _FIGURE_ANALYSIS_LOCK:
            for candidate in candidates[: self.config.max_figures]:
                try:
                    evidence = self._analyze_candidate(question, candidate, identity)
                except (OSError, RuntimeError, ValidationError, fitz.FileDataError) as error:
                    warnings.append(
                        f"Une figure de la page {candidate.element['page_number']} "
                        f"n’a pas pu être analysée ({type(error).__name__})."
                    )
                    continue
                processed += 1
                if evidence is not None:
                    admitted.append(evidence)
        if processed and not admitted:
            warnings.append(
                "Les figures examinées n’étaient pas assez pertinentes ou lisibles "
                "pour être intégrées à la réponse."
            )
        return FigureAnalysisBatch(
            processed_count=processed,
            admitted=admitted,
            warnings=warnings,
            duration_seconds=perf_counter() - started,
            model_name=identity.model_id,
        )

    def _candidates(
        self,
        question: str,
        references: Sequence[FigureSourceReference],
    ) -> list[_Candidate]:
        question_terms = {term.casefold() for term in _TERM.findall(question)}
        grouped: dict[tuple[CorpusScope, str], list[FigureSourceReference]] = {}
        for reference in references:
            grouped.setdefault((reference.scope, reference.article_id), []).append(reference)
        candidates: list[_Candidate] = []
        for (scope, article_id), scoped_references in grouped.items():
            paths = corpus_paths(self.settings, scope)
            database = Database(paths.database_path)
            article = database.article_details_by_ids([article_id]).get(article_id)
            if article is None:
                continue
            pdf_path = Path(str(article["pdf_path"])).resolve()
            allowed_roots = (paths.root.resolve(), paths.pdf_dir.resolve())
            if not pdf_path.is_file() or not any(
                pdf_path.is_relative_to(root) for root in allowed_roots
            ):
                continue
            for element in database.document_elements(article_id):
                if element["kind"] != "figure":
                    continue
                relations = element["text_relations"]
                matching = [
                    reference
                    for reference in scoped_references
                    if reference.page_start <= int(element["page_number"]) <= reference.page_end
                    or any(
                        relation["related_chunk_id"] == reference.chunk_id for relation in relations
                    )
                ]
                if not matching:
                    continue
                related_chunk_id = next(
                    (
                        int(relation["related_chunk_id"])
                        for relation in relations
                        if relation["related_chunk_id"] is not None
                        and any(
                            int(relation["related_chunk_id"]) == reference.chunk_id
                            for reference in matching
                        )
                    ),
                    matching[0].chunk_id,
                )
                context = " ".join(
                    [
                        str(element.get("original_caption") or ""),
                        *[str(relation["source_excerpt"]) for relation in relations],
                    ]
                )
                context_terms = {term.casefold() for term in _TERM.findall(context)}
                lexical_score = len(question_terms & context_terms) / max(len(question_terms), 1)
                candidates.append(
                    _Candidate(
                        scope=scope,
                        database=database,
                        article_id=article_id,
                        article_sha256=str(article["sha256"]),
                        pdf_path=pdf_path,
                        element=element,
                        related_chunk_id=related_chunk_id,
                        rank=min(reference.rank for reference in matching),
                        lexical_score=lexical_score,
                    )
                )
        candidates.sort(
            key=lambda item: (
                item.rank,
                -item.lexical_score,
                item.article_id,
                int(item.element["page_number"]),
                str(item.element["id"]),
            )
        )
        return candidates

    def _render(self, candidate: _Candidate) -> _RenderedCrop:
        with fitz.open(candidate.pdf_path) as document:
            page_index = int(candidate.element["page_number"]) - 1
            if not 0 <= page_index < document.page_count:
                raise RuntimeError("figure page is outside the PDF")
            page = document.load_page(page_index)
            bbox = fitz.Rect(*candidate.element["bbox"])
            clip = fitz.Rect(
                max(page.rect.x0, bbox.x0 - 18),
                max(page.rect.y0, bbox.y0 - 35),
                min(page.rect.x1, bbox.x1 + 18),
                min(page.rect.y1, bbox.y1 + 75),
            )
            if clip.is_empty or clip.width < 20 or clip.height < 20:
                raise RuntimeError("figure bounding box is invalid")
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(self.config.render_scale, self.config.render_scale),
                clip=clip,
                alpha=False,
            )
            return _RenderedCrop(
                image=pixmap.tobytes("png"),
                width=pixmap.width,
                height=pixmap.height,
            )

    def _analyze_candidate(
        self,
        question: str,
        candidate: _Candidate,
        model_identity: VisualModelIdentity,
    ) -> FigureEvidence | None:
        crop = self._render(candidate)
        image_sha256 = hashlib.sha256(crop.image).hexdigest()
        cleaned_question = " ".join(question.split())
        question_sha256 = hashlib.sha256(cleaned_question.encode()).hexdigest()
        artifact = VisualArtifactDescriptor(
            asset_id=uuid5(
                NAMESPACE_URL,
                f"ciderscholar:{candidate.element['id']}:{image_sha256}",
            ),
            source_document_sha256=candidate.article_sha256,
            image_sha256=image_sha256,
            page_number=int(candidate.element["page_number"]),
            bbox=tuple(float(value) for value in candidate.element["bbox"]),
            mime_type="image/png",
            byte_size=len(crop.image),
            pixel_width=crop.width,
            pixel_height=crop.height,
        )
        model_profile = re.sub(
            r"[^a-z0-9._-]+",
            "-",
            model_identity.model_id.casefold(),
        ).strip("-")[:80]
        request = ScientificFigureAnalysisRequest(
            artifact=artifact,
            context=VisualCaptionContext(
                kind="figure",
                original_caption=candidate.element.get("original_caption"),
                related_source_excerpts=[
                    str(relation["source_excerpt"])[:1_000]
                    for relation in candidate.element["text_relations"][:4]
                ],
            ),
            question=cleaned_question,
            prompt_version=_PROMPT_VERSION,
            model_profile=model_profile or "visual-default",
        )
        cached = candidate.database.figure_analysis(
            element_id=str(candidate.element["id"]),
            analysis_contract_sha256=request.idempotency_key,
            image_sha256=image_sha256,
            model_name=model_identity.model_id,
            model_revision=model_identity.model_revision,
        )
        if cached is not None:
            return self._evidence(candidate, cached) if cached["admitted"] else None

        request_started = perf_counter()
        response = self.gateway.analyze(request, image=crop.image)
        duration = perf_counter() - request_started
        if (
            response.image_sha256 != artifact.image_sha256
            or response.prompt_version != request.prompt_version
            or response.model_id != model_identity.model_id
            or response.model_revision != model_identity.model_revision
        ):
            raise RuntimeError("figure provider response provenance does not match the request")
        observation_text = self._observation_text(candidate.figure_label, response)
        validated = (
            response.supports_answer
            and response.relevance_score >= self.config.relevance_threshold
            and response.readability_score >= self.config.readability_threshold
            and bool(response.trends or response.summary)
        )
        validation_reason = (
            "automatic_thresholds_met"
            if validated
            else "insufficient_relevance_readability_or_support"
        )
        analysis_identity = hashlib.sha256(
            (
                str(candidate.element["id"])
                + request.idempotency_key
                + model_identity.model_id
                + model_identity.model_revision
            ).encode()
        ).hexdigest()[:24]
        stored = candidate.database.save_figure_analysis(
            {
                "id": f"figure-analysis-{analysis_identity}",
                "element_id": str(candidate.element["id"]),
                "source_document_sha256": candidate.article_sha256,
                "question_sha256": question_sha256,
                "image_sha256": image_sha256,
                "analysis_contract_sha256": request.idempotency_key,
                "model_name": response.model_id,
                "model_revision": response.model_revision,
                "prompt_version": response.prompt_version,
                "figure_type": response.figure_type,
                "relevance_score": response.relevance_score,
                "readability_score": response.readability_score,
                "supports_answer": response.supports_answer,
                "status": "validated" if validated else "rejected",
                "validation_reason": validation_reason,
                "observation_text": observation_text,
                "visible_variables": response.visible_variables,
                "visible_units": response.visible_units,
                "trends": response.trends,
                "limitations": [*response.limitations, *response.warnings],
                "duration_seconds": duration,
            }
        )
        return self._evidence(candidate, stored) if validated else None

    @staticmethod
    def _observation_text(
        figure_label: str,
        draft: _VisionDraft | ScientificFigureAnalysisResponse,
    ) -> str:
        parts = [f"{figure_label}. Observation visuelle locale : {draft.summary.strip()}"]
        if draft.visible_variables:
            parts.append(
                "Variables visibles : "
                + ", ".join(" ".join(value.split()) for value in draft.visible_variables)
                + "."
            )
        if draft.visible_units:
            parts.append(
                "Unités lisibles : "
                + ", ".join(" ".join(value.split()) for value in draft.visible_units)
                + "."
            )
        if draft.trends:
            parts.append(
                "Tendances visibles : "
                + " ".join(" ".join(value.split()) for value in draft.trends)
            )
        if draft.limitations:
            parts.append(
                "Limites de lecture : "
                + " ".join(" ".join(value.split()) for value in draft.limitations)
            )
        return " ".join(parts)[:4_000]

    @staticmethod
    def _evidence(candidate: _Candidate, stored: dict[str, Any]) -> FigureEvidence:
        observation = str(stored["observation_text"])
        return FigureEvidence(
            analysis_id=str(stored["id"]),
            scope=candidate.scope,
            article_id=candidate.article_id,
            element_id=str(candidate.element["id"]),
            local_element_id=str(candidate.element["local_element_id"]),
            related_chunk_id=candidate.related_chunk_id,
            page_number=int(candidate.element["page_number"]),
            figure_label=candidate.figure_label,
            observation_text=observation,
            observation_sha256=hashlib.sha256(observation.encode()).hexdigest(),
            image_sha256=str(stored["image_sha256"]),
            model_name=str(stored["model_name"]),
            relevance_score=float(stored["relevance_score"]),
            readability_score=float(stored["readability_score"]),
        )
