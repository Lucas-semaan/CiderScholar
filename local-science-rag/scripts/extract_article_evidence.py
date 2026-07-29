"""Rank local articles, select bounded passages, then persist source-validated evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from time import perf_counter

from app.config import load_settings
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.llm.argo_client import ArgoClient
from app.llm.article_evidence import (
    ArticleEvidenceExtractor,
    EvidencePassageSelector,
)
from app.retrieval.article_ranking import ArticleRankingService
from app.retrieval.hybrid_search import HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Scientific question")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--query-id", help="Existing or explicit resumable query UUID")
    parser.add_argument("--article-count", type=int, help="Articles to analyze")
    parser.add_argument("--article-id", action="append", dest="article_ids")
    parser.add_argument("--variant", action="append", dest="variants")
    parser.add_argument("--central-concept", action="append", dest="central_concepts")
    parser.add_argument(
        "--diversity",
        choices=("none", "theme", "year", "journal", "balanced"),
    )
    parser.add_argument("--passages", type=int, help="Passages per article (3-8)")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print strict JSON")
    return parser


def _parameter_hash(arguments: argparse.Namespace, settings_dump: dict) -> str:
    value = {
        "variants": arguments.variants or [],
        "central_concepts": arguments.central_concepts or [],
        "diversity": arguments.diversity,
        "passages": arguments.passages,
        "retrieval": settings_dump["retrieval"],
        "article_ranking": settings_dump["article_ranking"],
        "evidence": settings_dump["evidence"],
    }
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resumed_items(
    database: Database, query_id: str, article_ids: list[str]
) -> tuple[list[dict[str, object]], list[str]]:
    details = database.article_details_by_ids(article_ids)
    completed: list[dict[str, object]] = []
    pending: list[str] = []
    for article_id in article_ids:
        evidence = database.load_article_evidence(query_id, article_id)
        run = database.article_evidence_run(query_id, article_id)
        metadata = details.get(article_id)
        if evidence is None or run is None or metadata is None:
            pending.append(article_id)
            continue
        chunk_ids = [int(value) for value in json.loads(run["selected_chunk_ids"])]
        chunks = database.chunk_details_by_ids(chunk_ids)
        completed.append(
            {
                "article": {
                    "article_id": article_id,
                    "title": str(metadata["title"]),
                    "doi": metadata["doi"],
                    "publication_year": metadata["publication_year"],
                    "language": metadata["language"],
                    "resumed_metadata": True,
                },
                "extraction": {
                    "query_id": query_id,
                    "article_id": article_id,
                    "evidence": evidence.model_dump(mode="json"),
                    "selected_passages": [
                        {
                            "chunk_id": chunk_id,
                            "section": chunks[chunk_id]["section"],
                            "page_start": chunks[chunk_id]["page_start"],
                            "page_end": chunks[chunk_id]["page_end"],
                            "selection_score": 0.0,
                            "selection_reasons": ["resumed from SQLite"],
                        }
                        for chunk_id in chunk_ids
                        if chunk_id in chunks
                    ],
                    "attempts": 0,
                    "resumed_from_database": True,
                    "generation_metrics": [],
                    "duration_seconds": 0.0,
                },
            }
        )
    return completed, pending


def _print_output(output: dict[str, object], *, strict_json: bool, offline: bool) -> None:
    if strict_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    results = output["results"]
    errors = output["errors"]
    assert isinstance(results, list) and isinstance(errors, list)
    print("MODE HORS LIGNE ACTIF" if offline else "MODE EN LIGNE")
    print(
        f"question={output['query_id']} | articles terminés={len(results)} | "
        f"échecs={len(errors)} | durée={output['duration_seconds']:.3f}s"
    )
    for item in results:
        assert isinstance(item, dict)
        article = item["article"]
        extraction = item["extraction"]
        assert isinstance(article, dict) and isinstance(extraction, dict)
        evidence = extraction["evidence"]
        assert isinstance(evidence, dict)
        findings = evidence["findings"]
        assert isinstance(findings, list)
        print(
            f"- {article['title']} | pertinence={evidence['relevance_score']:.2f} | "
            f"preuves={len(findings)} | tentatives={extraction['attempts']}"
        )
        for finding in findings:
            assert isinstance(finding, dict)
            print(f"  [{article['article_id']}, p. {finding['page_start']}] {finding['claim']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings(args.config)
    database = Database(settings.paths.database_path)
    database.initialize()
    started = perf_counter()

    query_id = args.query_id or str(uuid.uuid4())
    existing_query = database.query_by_id(query_id)
    existing_article_ids: list[str] | None = None
    if existing_query is not None:
        if str(existing_query["original_query"]) != args.query.strip():
            print("Le query_id appartient à une autre question.", file=sys.stderr)
            return 2
        existing_article_ids = json.loads(existing_query["selected_article_ids"])

    results: list[dict[str, object]] = []
    if existing_article_ids and not args.no_resume:
        results, pending_article_ids = _resumed_items(database, query_id, existing_article_ids)
        if not pending_article_ids:
            output = {
                "query_id": query_id,
                "query": args.query.strip(),
                "selected_article_count": len(existing_article_ids),
                "completed_article_count": len(results),
                "failed_article_count": 0,
                "duration_seconds": perf_counter() - started,
                "results": results,
                "errors": [],
            }
            _print_output(output, strict_json=args.json, offline=settings.app.offline_mode)
            return 0
        existing_article_ids = pending_article_ids

    requested_article_ids = args.article_ids or existing_article_ids
    requested_count = args.article_count
    if requested_article_ids:
        requested_count = len(requested_article_ids)

    backend = SentenceTransformerBackend(settings)
    ranking = ArticleRankingService(
        settings,
        database,
        HybridSearchService(
            settings,
            database,
            LexicalSearchService(settings, database),
            VectorSearchService(
                database,
                backend,
                QdrantLocalIndex(settings),
            ),
        ),
    )
    try:
        ranked = ranking.search(
            args.query,
            query_variants=args.variants,
            article_count=requested_count,
            diversity_mode=args.diversity,
            central_concepts=args.central_concepts,
            article_ids=requested_article_ids,
        )
    finally:
        # E5 and Qdrant are released before the active generation backend is used.
        ranking.close()
    if not ranked.articles:
        print("Aucun article classé pour cette question.", file=sys.stderr)
        return 2

    selected_ids = [article.article_id for article in ranked.articles]
    if existing_query is None:
        database.create_query(
            query_id=query_id,
            original_query=args.query.strip(),
            expanded_queries=args.variants or [],
            selected_article_ids=selected_ids,
            model_version=settings.argo.model,
            parameters_hash=_parameter_hash(args, settings.model_dump(mode="json")),
        )
    print(f"query_id={query_id}", file=sys.stderr)

    selector = EvidencePassageSelector(settings, database)
    errors: list[dict[str, str]] = []
    with ArgoClient(settings) as llm:
        extractor = ArticleEvidenceExtractor(settings, database, llm)
        for article in ranked.articles:
            try:
                passages = selector.select(
                    query=args.query,
                    article_id=article.article_id,
                    ranked_chunk_ids=article.top_chunk_ids,
                    passage_count=args.passages,
                )
                result = extractor.extract(
                    query=args.query,
                    article_id=article.article_id,
                    passages=passages,
                    query_id=query_id,
                    resume=not args.no_resume,
                )
                results.append(
                    {
                        "article": article.model_dump(mode="json"),
                        "extraction": result.model_dump(mode="json"),
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "article_id": article.article_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:1000],
                    }
                )
                if args.stop_on_error:
                    break

    duration = perf_counter() - started
    database.update_query_duration(query_id, duration)
    total_selected_count = (
        len(json.loads(existing_query["selected_article_ids"]))
        if existing_query is not None
        else len(ranked.articles)
    )
    output: dict[str, object] = {
        "query_id": query_id,
        "query": args.query.strip(),
        "selected_article_count": total_selected_count,
        "completed_article_count": len(results),
        "failed_article_count": len(errors),
        "duration_seconds": duration,
        "results": results,
        "errors": errors,
    }
    _print_output(output, strict_json=args.json, offline=settings.app.offline_mode)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
