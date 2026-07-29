"""Offline article-retrieval benchmark with optional synthesis traceability checks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.sqlite import Database
from app.evaluation.metrics import (
    RankingMetrics,
    TraceabilityMetrics,
    combine_traceability,
    concept_recall,
    ranking_metrics,
    traceability_metrics,
)
from app.models.synthesis import FinalSynthesis
from app.retrieval.article_ranking import ArticleRankingResponse, DiversityMode


class EvaluationCase(BaseModel):
    """One human-labelled scientific retrieval question."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    expected_article_ids: list[str] = Field(min_length=1)
    acceptable_article_ids: list[str] = Field(default_factory=list)
    expected_concepts: list[str] = Field(default_factory=list)
    query_id: str | None = None

    @field_validator("expected_article_ids", "acceptable_article_ids", "expected_concepts")
    @classmethod
    def clean_unique_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("evaluation values cannot be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("evaluation values cannot be duplicated")
        return cleaned

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("evaluation question cannot be empty")
        return cleaned

    @field_validator("query_id")
    @classmethod
    def clean_query_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query_id cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def distinct_relevance_sets(self) -> EvaluationCase:
        overlap = set(self.expected_article_ids).intersection(self.acceptable_article_ids)
        if overlap:
            raise ValueError("expected and acceptable article identifiers must be disjoint")
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvaluationCase] = Field(min_length=1)


class AggregateRankingMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    precision_at_k: float = Field(ge=0.0, le=1.0)
    recall_at_k: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    ndcg_at_k: float = Field(ge=0.0, le=1.0)
    concept_recall: float = Field(ge=0.0, le=1.0)


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    expected_article_ids: list[str]
    acceptable_article_ids: list[str]
    ranked_article_ids: list[str]
    returned_article_count: int = Field(ge=0)
    metrics: RankingMetrics
    concept_recall: float = Field(ge=0.0, le=1.0)
    duration_seconds: float = Field(ge=0.0)
    synthesis_query_id: str | None = None
    traceability: TraceabilityMetrics | None = None


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: str
    corpus_version: str
    model_name: str
    top_k: int = Field(ge=1)
    diversity_mode: DiversityMode
    case_count: int = Field(ge=1)
    aggregate: AggregateRankingMetrics
    traceability: TraceabilityMetrics
    duration_seconds: float = Field(ge=0.0)
    peak_process_rss_gb: float | None = Field(default=None, ge=0.0)
    peak_system_used_gb: float | None = Field(default=None, ge=0.0)
    cases: list[CaseEvaluation]


class RankingBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        article_count: int | None = None,
        diversity_mode: DiversityMode | None = None,
        central_concepts: Sequence[str] | None = None,
    ) -> ArticleRankingResponse: ...


class PeakMemoryMonitor:
    """Sample process and system RAM in a low-overhead background thread."""

    def __init__(self, *, interval_seconds: float = 0.1) -> None:
        if interval_seconds <= 0:
            raise ValueError("memory sampling interval must be positive")
        self.interval_seconds = interval_seconds
        self.peak_process_rss_gb: float | None = None
        self.peak_system_used_gb: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            import psutil
        except ImportError:  # pragma: no cover - required in the supported installation
            return
        process_rss = psutil.Process(os.getpid()).memory_info().rss / (1024**3)
        system_used = psutil.virtual_memory().used / (1024**3)
        self.peak_process_rss_gb = max(self.peak_process_rss_gb or 0.0, process_rss)
        self.peak_system_used_gb = max(self.peak_system_used_gb or 0.0, system_used)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self) -> PeakMemoryMonitor:
        self._sample()
        self._thread = threading.Thread(
            target=self._run, name="benchmark-memory-monitor", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds * 2, 0.2))
        self._sample()


def load_evaluation_cases(path: str | Path) -> list[EvaluationCase]:
    """Load either a JSON array of cases or an object containing a `cases` array."""

    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases = [EvaluationCase.model_validate(value) for value in payload]
        if not cases:
            raise ValueError("evaluation suite cannot be empty")
        return cases
    return EvaluationSuite.model_validate(payload).cases


def corpus_fingerprint(database: Database) -> str:
    """Hash authoritative article identities and content hashes in deterministic order."""

    with closing(database.connect()) as connection:
        rows = connection.execute("SELECT id, sha256, validation_status FROM articles ORDER BY id")
        payload = [tuple(str(value) for value in row) for row in rows]
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_demo_cases(database: Database) -> list[EvaluationCase]:
    """Build reproducible labelled questions for the three generated demonstration articles."""

    by_title = {str(row["title"]): str(row["id"]) for row in database.list_articles(limit=5000)}
    definitions = (
        (
            "How does fermentation temperature affect cider aroma?",
            "Temperature and Aroma Formation in Synthetic Cider Fermentation",
            ["fermentation temperature", "aroma formation"],
        ),
        (
            "How does nitrogen availability affect synthetic yeast fermentation kinetics?",
            "Nitrogen Availability and Synthetic Yeast Kinetics",
            ["nitrogen availability", "fermentation time"],
        ),
        (
            "Quelle est la stabilité des polyphénols pendant le stockage ?",
            "Stockage local et stabilité fictive des polyphénols",
            ["polyphénols", "stockage"],
        ),
    )
    missing = [title for _question, title, _concepts in definitions if title not in by_title]
    if missing:
        raise ValueError(
            "demonstration corpus is incomplete; missing titles: " + ", ".join(missing)
        )
    return [
        EvaluationCase(
            question=question,
            expected_article_ids=[by_title[title]],
            expected_concepts=concepts,
        )
        for question, title, concepts in definitions
    ]


def _final_statement_evidence(synthesis: FinalSynthesis) -> list[list[str]]:
    values: list[list[str]] = []
    for field_name in (
        "direct_answer",
        "consensus",
        "convergent_results",
        "contradictory_results",
        "quantitative_results",
    ):
        values.extend(statement.evidence_ids for statement in getattr(synthesis, field_name))
    return values


class BenchmarkRunner:
    """Run labelled questions sequentially against one already-constructed local ranker."""

    def __init__(
        self,
        database: Database,
        ranker: RankingBackend,
        *,
        model_name: str,
        top_k: int = 20,
        diversity_mode: DiversityMode = "balanced",
        monitor_factory: Callable[[], PeakMemoryMonitor] = PeakMemoryMonitor,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.database = database
        self.ranker = ranker
        self.model_name = model_name
        self.top_k = top_k
        self.diversity_mode = diversity_mode
        self.monitor_factory = monitor_factory

    def _matching_query_id(self, case: EvaluationCase) -> str | None:
        if case.query_id is not None:
            return case.query_id if self.database.load_final_synthesis(case.query_id) else None
        normalized_question = " ".join(case.question.casefold().split())
        for row in self.database.list_query_summaries(limit=1000):
            stored_question = " ".join(str(row["original_query"]).casefold().split())
            query_id = str(row["id"])
            if stored_question == normalized_question and self.database.load_final_synthesis(
                query_id
            ):
                return query_id
        return None

    def _traceability(self, query_id: str | None) -> TraceabilityMetrics | None:
        if query_id is None:
            return None
        synthesis = self.database.load_final_synthesis(query_id)
        if synthesis is None:
            return None
        allowed_ids = {
            str(row["evidence_id"]) for row in self.database.evidence_records_for_query(query_id)
        }
        return traceability_metrics(_final_statement_evidence(synthesis), allowed_ids)

    def _observed_texts(self, response: ArticleRankingResponse) -> list[str]:
        chunk_ids = [
            chunk_id for article in response.articles for chunk_id in article.top_chunk_ids
        ]
        chunks = self.database.chunks_by_ids(chunk_ids)
        texts: list[str] = []
        for article in response.articles:
            texts.append(article.title)
            if article.abstract:
                texts.append(article.abstract)
            texts.extend(
                str(chunks[chunk_id]["text"])
                for chunk_id in article.top_chunk_ids
                if chunk_id in chunks
            )
        return texts

    def run(self, cases: Sequence[EvaluationCase]) -> BenchmarkReport:
        if not cases:
            raise ValueError("evaluation suite cannot be empty")
        started = perf_counter()
        results: list[CaseEvaluation] = []
        traceability_results: list[TraceabilityMetrics] = []
        monitor = self.monitor_factory()
        with monitor:
            for case in cases:
                case_started = perf_counter()
                response = self.ranker.search(
                    case.question,
                    article_count=self.top_k,
                    diversity_mode=self.diversity_mode,
                )
                ranked_ids = [article.article_id for article in response.articles]
                metrics = ranking_metrics(
                    ranked_ids,
                    case.expected_article_ids,
                    case.acceptable_article_ids,
                    k=self.top_k,
                )
                query_id = self._matching_query_id(case)
                traceability = self._traceability(query_id)
                if traceability is not None:
                    traceability_results.append(traceability)
                results.append(
                    CaseEvaluation(
                        question=case.question,
                        expected_article_ids=case.expected_article_ids,
                        acceptable_article_ids=case.acceptable_article_ids,
                        ranked_article_ids=ranked_ids,
                        returned_article_count=len(ranked_ids),
                        metrics=metrics,
                        concept_recall=concept_recall(
                            case.expected_concepts, self._observed_texts(response)
                        ),
                        duration_seconds=perf_counter() - case_started,
                        synthesis_query_id=query_id,
                        traceability=traceability,
                    )
                )

        count = len(results)
        aggregate = AggregateRankingMetrics(
            precision_at_k=sum(value.metrics.precision_at_k for value in results) / count,
            recall_at_k=sum(value.metrics.recall_at_k for value in results) / count,
            mean_reciprocal_rank=(
                sum(value.metrics.mean_reciprocal_rank for value in results) / count
            ),
            ndcg_at_k=sum(value.metrics.ndcg_at_k for value in results) / count,
            concept_recall=sum(value.concept_recall for value in results) / count,
        )
        return BenchmarkReport(
            created_at=datetime.now(UTC).isoformat(),
            corpus_version=corpus_fingerprint(self.database),
            model_name=self.model_name,
            top_k=self.top_k,
            diversity_mode=self.diversity_mode,
            case_count=count,
            aggregate=aggregate,
            traceability=combine_traceability(traceability_results),
            duration_seconds=perf_counter() - started,
            peak_process_rss_gb=monitor.peak_process_rss_gb,
            peak_system_used_gb=monitor.peak_system_used_gb,
            cases=results,
        )


def _rate(value: float | None) -> str:
    return "non évalué" if value is None else f"{value:.2%}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown_report(report: BenchmarkReport) -> str:
    """Render a standalone, human-readable benchmark report."""

    lines = [
        "# Rapport de benchmark Local Science RAG",
        "",
        f"- Date UTC : `{report.created_at}`",
        f"- Version du corpus : `{report.corpus_version}`",
        f"- Modèle d’embeddings : `{report.model_name}`",
        f"- Cas évalués : {report.case_count}",
        f"- Profondeur : {report.top_k}",
        f"- Diversité : `{report.diversity_mode}`",
        "",
        "## Mesures agrégées",
        "",
        "| Mesure | Valeur |",
        "|---|---:|",
        f"| Précision@{report.top_k} | {report.aggregate.precision_at_k:.4f} |",
        f"| Rappel@{report.top_k} | {report.aggregate.recall_at_k:.4f} |",
        f"| Mean Reciprocal Rank | {report.aggregate.mean_reciprocal_rank:.4f} |",
        f"| nDCG@{report.top_k} | {report.aggregate.ndcg_at_k:.4f} |",
        f"| Rappel des concepts | {report.aggregate.concept_recall:.4f} |",
        "",
        "## Traçabilité des synthèses persistées",
        "",
        f"- Synthèses évaluées : {report.traceability.evaluated_synthesis_count}",
        f"- Citations traçables : {_rate(report.traceability.traceable_citation_rate)} ",
        f"  ({report.traceability.traceable_citations}/{report.traceability.total_citations})",
        f"- Affirmations sans preuve : {_rate(report.traceability.unsupported_assertion_rate)} ",
        f"  ({report.traceability.unsupported_assertions}/{report.traceability.total_assertions})",
        "",
        "## Temps et mémoire",
        "",
        f"- Durée totale : {report.duration_seconds:.3f} s",
        "- Pic RSS du processus : "
        + (
            f"{report.peak_process_rss_gb:.3f} Go"
            if report.peak_process_rss_gb is not None
            else "indisponible"
        ),
        "- Pic mémoire système utilisée : "
        + (
            f"{report.peak_system_used_gb:.3f} Go"
            if report.peak_system_used_gb is not None
            else "indisponible"
        ),
        "",
        "## Résultats par cas",
        "",
        f"| Question | P@{report.top_k} | R@{report.top_k} | MRR | nDCG | Concepts | Durée |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in report.cases:
        lines.append(
            f"| {_cell(result.question)} | {result.metrics.precision_at_k:.4f} | "
            f"{result.metrics.recall_at_k:.4f} | "
            f"{result.metrics.mean_reciprocal_rank:.4f} | "
            f"{result.metrics.ndcg_at_k:.4f} | {result.concept_recall:.4f} | "
            f"{result.duration_seconds:.3f} s |"
        )
    lines.extend(
        [
            "",
            "Les articles `expected` ont un gain nDCG de 2, les articles `acceptable` un "
            "gain de 1.",
            "La précision et le MRR considèrent les deux ensembles pertinents ; le rappel "
            "mesure les",
            "articles attendus. Une synthèse n’est évaluée que si un `query_id` terminé "
            "est fourni ou",
            "si une question terminée identique existe dans SQLite.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_benchmark_outputs(
    report: BenchmarkReport,
    *,
    markdown_path: str | Path,
    json_path: str | Path | None = None,
) -> tuple[Path, Path | None]:
    """Persist Markdown and optional JSON atomically."""

    markdown = Path(markdown_path).resolve()
    _atomic_write(markdown, render_markdown_report(report))
    serialized_path: Path | None = None
    if json_path is not None:
        serialized_path = Path(json_path).resolve()
        _atomic_write(serialized_path, report.model_dump_json(indent=2) + "\n")
    return markdown, serialized_path
