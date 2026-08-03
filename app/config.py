"""Typed, local-first application configuration."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AppConfig(BaseModel):
    """Network and privacy defaults."""

    model_config = ConfigDict(extra="forbid")

    host: Literal["127.0.0.1"] = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1024, le=65535)
    offline_mode: bool = False
    allow_bibliographic_apis: bool = False
    allow_publisher_automation: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    chat_worker_concurrency: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def enforce_offline_mode(self) -> AppConfig:
        if self.offline_mode and (self.allow_bibliographic_apis or self.allow_publisher_automation):
            raise ValueError("network connectors must be disabled while offline_mode is active")
        return self


class PathConfig(BaseModel):
    """All persistent application paths remain under data_dir."""

    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("data")
    pdf_dir: Path = Path("data/pdf")
    extracted_dir: Path = Path("data/extracted")
    qdrant_dir: Path = Path("data/qdrant")
    models_dir: Path = Path("data/models")
    database_path: Path = Path("data/database/science_rag.sqlite3")
    cache_dir: Path = Path("data/cache")
    exports_dir: Path = Path("data/exports")

    @property
    def common_dir(self) -> Path:
        return self.data_dir / "common"

    @property
    def common_pdf_dir(self) -> Path:
        return self.common_dir / "pdf"

    @property
    def common_extracted_dir(self) -> Path:
        return self.common_dir / "extracted"

    @property
    def common_qdrant_dir(self) -> Path:
        return self.common_dir / "qdrant"

    @property
    def common_database_path(self) -> Path:
        return self.common_dir / "database" / "science_rag.sqlite3"

    @property
    def private_dir(self) -> Path:
        return self.data_dir / "private"

    @property
    def private_pdf_dir(self) -> Path:
        return self.private_dir / "pdf"

    @property
    def private_extracted_dir(self) -> Path:
        return self.private_dir / "extracted"

    @property
    def private_qdrant_dir(self) -> Path:
        return self.private_dir / "qdrant"

    @property
    def private_database_path(self) -> Path:
        return self.private_dir / "database" / "science_rag.sqlite3"

    def resolved(self, project_dir: Path) -> PathConfig:
        values: dict[str, Path] = {}
        for name, value in self:
            values[name] = value if value.is_absolute() else (project_dir / value).resolve()
        resolved = PathConfig(**values)
        root = resolved.data_dir.resolve()
        for name, value in resolved:
            try:
                value.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"paths.{name} must remain inside paths.data_dir") from exc
        return resolved

    def create(self) -> None:
        for name, path in self:
            directory = path.parent if name == "database_path" else path
            directory.mkdir(parents=True, exist_ok=True)
        for path in (
            self.common_pdf_dir,
            self.common_extracted_dir,
            self.common_qdrant_dir,
            self.common_database_path.parent,
            self.private_pdf_dir,
            self.private_extracted_dir,
            self.private_qdrant_dir,
            self.private_database_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


class IngestionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_tokens: int = Field(default=500, ge=100, le=2000)
    max_tokens: int = Field(default=750, ge=100, le=3000)
    overlap_tokens: int = Field(default=80, ge=0, le=500)
    min_page_text_characters: int = Field(default=25, ge=0)
    min_text_page_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    ocr_language: str = Field(default="fr-FR", pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    ocr_min_confidence: float = Field(default=0.75, ge=0.5, le=1.0)
    metadata_scan_pages: int = Field(default=3, ge=1, le=10)
    local_import_validation_status: Literal["validated", "awaiting_validation"] = "validated"

    @model_validator(mode="after")
    def validate_chunk_sizes(self) -> IngestionConfig:
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        return self


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = "intfloat/multilingual-e5-base"
    optional_large_model: str = "BAAI/bge-m3"
    batch_size: int = Field(default=8, ge=1, le=64)
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    normalize: bool = True
    max_sequence_length: int = Field(default=512, ge=64, le=8192)
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "
    local_files_only: Literal[True] = True
    trust_remote_code: Literal[False] = False


class RerankerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    batch_size: int = Field(default=4, ge=1, le=32)
    candidate_limit: int = Field(default=40, ge=1, le=200)
    device: Literal["cpu"] = "cpu"
    local_files_only: Literal[True] = True
    trust_remote_code: Literal[False] = False


class DeepResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    rrf_k: int = Field(default=60, ge=1, le=1000)
    rrf_candidate_limit: int = Field(default=80, ge=1, le=80)
    cross_encoder_candidate_limit: int = Field(default=40, ge=1, le=40)
    retained_fragment_limit: int = Field(default=12, ge=1, le=40)
    contextual_summary_enabled: bool = False
    contextual_summary_top_k: int = Field(default=12, ge=1, le=12)
    contextual_relevance_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    contextual_relevance_observations_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_cascade_limits(self) -> DeepResearchConfig:
        if self.cross_encoder_candidate_limit > self.rrf_candidate_limit:
            raise ValueError("cross-encoder limit cannot exceed the RRF candidate limit")
        if self.retained_fragment_limit > self.cross_encoder_candidate_limit:
            raise ValueError("retained fragment limit cannot exceed the cross-encoder limit")
        if (
            self.contextual_summary_enabled
            and self.contextual_relevance_observations_sha256 is None
        ):
            raise ValueError("contextual summary requires a pinned CiderQA calibration hash")
        return self


class QdrantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_name: str = Field(
        default="science_chunks", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$"
    )
    distance: Literal["cosine"] = "cosine"
    on_disk_vectors: bool = True
    on_disk_payload: bool = True
    default_search_limit: int = Field(default=100, ge=1, le=1000)
    score_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lexical_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    vector_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    reranker_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    default_article_count: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1)
    lexical_default_limit: int = Field(default=100, ge=1, le=1000)
    lexical_max_query_characters: int = Field(default=2000, ge=100, le=20000)
    lexical_max_terms: int = Field(default=24, ge=1, le=100)
    lexical_min_token_length: int = Field(default=2, ge=1, le=10)
    lexical_prefix_matching: bool = True
    lexical_prefix_min_length: int = Field(default=4, ge=2, le=20)
    lexical_section_weight: float = Field(default=1.5, ge=0.0, le=10.0)
    lexical_text_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    hybrid_candidate_limit: int = Field(default=200, ge=10, le=1000)
    hybrid_default_limit: int = Field(default=100, ge=1, le=1000)
    hybrid_max_query_variants: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def validate_weights(self) -> RetrievalConfig:
        total = self.lexical_weight + self.vector_weight + self.reranker_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("retrieval weights must add up to 1.0")
        return self


class ArticleRankingConfig(BaseModel):
    """Explainable aggregation and optional diversity for distinct articles."""

    model_config = ConfigDict(extra="forbid")

    best_fragment_weight: float = Field(default=0.40, ge=0.0, le=1.0)
    top_three_mean_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    title_relevance_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    abstract_relevance_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    central_concept_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    top_chunks_per_article: int = Field(default=8, ge=3, le=8)
    diversity_enabled: bool = True
    diversity_mode: Literal["none", "theme", "year", "journal", "balanced"] = "balanced"
    diversity_strength: float = Field(default=0.15, ge=0.0, le=1.0)
    near_duplicate_threshold: float = Field(default=0.90, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_weights(self) -> ArticleRankingConfig:
        total = (
            self.best_fragment_weight
            + self.top_three_mean_weight
            + self.title_relevance_weight
            + self.abstract_relevance_weight
            + self.central_concept_weight
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError("article ranking weights must add up to 1.0")
        return self


class ArgoConfig(BaseModel):
    """INRAE ARGO OpenAI-compatible chat endpoint."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://chatbot.argo.inrae.fr/api"
    model: str = "chat-gpt-oss-120b"
    api_key_env: str = Field(
        default="LOCAL_SCIENCE_RAG_ARGO_API_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_input_characters: int = Field(default=64000, ge=1000, le=250000)
    max_output_tokens: int = Field(default=8192, ge=256, le=16384)
    request_timeout_seconds: int = Field(default=300, ge=10)
    model_validation_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    verify_tls: Literal[True] = True

    @field_validator("base_url")
    @classmethod
    def enforce_official_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "chatbot.argo.inrae.fr"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api"
        ):
            raise ValueError(
                "argo.base_url must be the official https://chatbot.argo.inrae.fr/api endpoint"
            )
        return "https://chatbot.argo.inrae.fr/api"

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("ARGO model name must be non-empty and contain no spaces")
        return cleaned


class FigureAnalysisConfig(BaseModel):
    """Bounded local vision analysis used only after textual retrieval."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-vl:8b-instruct"
    max_figures: int = Field(default=5, ge=1, le=10)
    relevance_threshold: float = Field(default=0.80, ge=0.5, le=1.0)
    readability_threshold: float = Field(default=0.70, ge=0.5, le=1.0)
    render_scale: float = Field(default=2.0, ge=1.0, le=4.0)
    request_timeout_seconds: int = Field(default=240, ge=30, le=900)
    estimated_min_seconds: int = Field(default=720, ge=30, le=3600)
    estimated_max_seconds: int = Field(default=1080, ge=30, le=3600)

    @field_validator("base_url")
    @classmethod
    def enforce_loopback_ollama(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port not in {None, 11434}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/")
        ):
            raise ValueError("figure_analysis.base_url must be the local Ollama endpoint")
        return "http://127.0.0.1:11434"

    @field_validator("model")
    @classmethod
    def validate_local_model_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("figure analysis model name is invalid")
        return cleaned

    @model_validator(mode="after")
    def validate_time_estimate(self) -> FigureAnalysisConfig:
        if self.estimated_max_seconds < self.estimated_min_seconds:
            raise ValueError("figure analysis maximum estimate must follow the minimum")
        return self


BibliographicSource = Literal["crossref", "europe_pmc", "openalex", "clarivate", "elsevier"]


class BibliographicConfig(BaseModel):
    """Explicit allow-list for official bibliographic metadata APIs."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    sources: list[BibliographicSource] = Field(
        default_factory=lambda: [
            "crossref",
            "europe_pmc",
            "openalex",
            "clarivate",
            "elsevier",
        ]
    )
    per_source_limit: int = Field(default=10, ge=1, le=50)
    timeout_seconds: float = Field(default=20.0, ge=3.0, le=120.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    request_delay_seconds: float = Field(default=0.35, ge=0.0, le=10.0)
    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key_env: str = "OPENALEX_KEY"
    crossref_base_url: str = "https://api.crossref.org"
    crossref_email: str = ""
    europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    elsevier_base_url: str = "https://api.elsevier.com/content/search/scopus"
    elsevier_api_key_env: str = "ELSEVIER_KEY"
    clarivate_base_url: str = "https://api.clarivate.com/apis/wos-starter/v1"
    clarivate_api_key_env: str = "CLARIVATE_API_KEY"
    clarivate_api_mode: Literal["starter", "expanded"] = "starter"
    clarivate_database: str = "WOS"
    clarivate_expanded_option_view: Literal["SR", "FR"] = "SR"

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, values: list[BibliographicSource]) -> list[BibliographicSource]:
        if not values:
            raise ValueError("at least one bibliographic source is required")
        return list(dict.fromkeys(values))

    @field_validator(
        "openalex_api_key_env",
        "elsevier_api_key_env",
        "clarivate_api_key_env",
    )
    @classmethod
    def validate_key_environment_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", value):
            raise ValueError("bibliographic API key environment name is invalid")
        return value

    @model_validator(mode="after")
    def enforce_official_urls(self) -> BibliographicConfig:
        expected = {
            "openalex_base_url": "https://api.openalex.org",
            "crossref_base_url": "https://api.crossref.org",
            "europe_pmc_base_url": ("https://www.ebi.ac.uk/europepmc/webservices/rest"),
            "elsevier_base_url": ("https://api.elsevier.com/content/search/scopus"),
        }
        for name, required in expected.items():
            if getattr(self, name).rstrip("/") != required:
                raise ValueError(f"bibliographic.{name} must use {required}")
            setattr(self, name, required)
        clarivate_urls = {
            "starter": "https://api.clarivate.com/apis/wos-starter/v1",
            "expanded": "https://wos-api.clarivate.com/api/wos",
        }
        required_clarivate_url = clarivate_urls[self.clarivate_api_mode]
        if self.clarivate_base_url.rstrip("/") != required_clarivate_url:
            raise ValueError(
                "bibliographic.clarivate_base_url does not match "
                f"clarivate_api_mode={self.clarivate_api_mode}"
            )
        self.clarivate_base_url = required_clarivate_url
        if self.crossref_email and (
            "@" not in self.crossref_email
            or any(character.isspace() for character in self.crossref_email)
        ):
            raise ValueError("bibliographic.crossref_email is invalid")
        return self


class HarvestConfig(BaseModel):
    """Bounded local harvesting profile for cider-design corpora."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    profile: str = Field(default="cider_design", pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    cadence_hours: int = Field(default=168, ge=24, le=24 * 31)
    per_source_limit: int = Field(default=3, ge=1, le=50)
    request_delay_seconds: float = Field(default=1.0, ge=0.5, le=10.0)
    max_records_per_run: int = Field(default=120, ge=5, le=5000)
    openalex_free_only: Literal[True] = True
    openalex_max_cost_usd_per_run: float = Field(default=0.05, gt=0.0, le=1.0)
    vector_collection_name: str = Field(
        default="bibliographic_abstracts",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$",
    )


FullTextSource = Literal[
    "europe_pmc",
    "istex",
    "core",
    "hal",
    "semantic_scholar",
    "openalex",
    "unpaywall",
    "doaj",
    "crossref",
    "elsevier",
]


class FullTextConfig(BaseModel):
    """Bounded DOI-first acquisition from official full-text discovery APIs."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    sources: list[FullTextSource] = Field(
        default_factory=lambda: [
            "europe_pmc",
            "istex",
            "core",
            "hal",
            "semantic_scholar",
            "openalex",
            "unpaywall",
            "doaj",
            "crossref",
            "elsevier",
        ]
    )
    timeout_seconds: float = Field(default=30.0, ge=3.0, le=120.0)
    request_delay_seconds: float = Field(default=0.2, ge=0.0, le=10.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    batch_size: int = Field(default=25, ge=1, le=100)
    max_download_bytes: int = Field(default=104_857_600, ge=1_048_576, le=524_288_000)
    max_downloads_per_run: int = Field(default=500, ge=1, le=5000)
    availability_cache_hours: int = Field(default=168, ge=1, le=24 * 365)
    timeout_cooldown_hours: int = Field(default=6, ge=1, le=24 * 30)
    default_rate_limit_cooldown_hours: int = Field(default=1, ge=1, le=24 * 30)
    protected_host_cooldown_hours: int = Field(default=720, ge=24, le=24 * 365)
    core_request_delay_seconds: float = Field(default=10.0, ge=10.0, le=60.0)
    repository_request_delay_seconds: float = Field(default=1.0, ge=0.5, le=10.0)
    europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    istex_base_url: str = "https://api.istex.fr"
    istex_token_env: str = Field(
        default="ISTEX_API_TOKEN",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    core_base_url: str = "https://api.core.ac.uk/v3"
    core_api_key_env: str = Field(
        default="CORE_API_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    hal_base_url: str = "https://api.archives-ouvertes.fr/search"
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_api_key_env: str = Field(
        default="SEMANTIC_SCHOLAR_API_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key_env: str = Field(
        default="OPENALEX_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    unpaywall_base_url: str = "https://api.unpaywall.org/v2"
    doaj_base_url: str = "https://doaj.org/api/search/articles"
    crossref_base_url: str = "https://api.crossref.org"
    elsevier_article_base_url: str = "https://api.elsevier.com/content/article/doi"
    elsevier_api_key_env: str = Field(
        default="ELSEVIER_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )

    @field_validator("sources")
    @classmethod
    def unique_full_text_sources(cls, values: list[FullTextSource]) -> list[FullTextSource]:
        if not values:
            raise ValueError("at least one full-text source is required")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def enforce_official_full_text_urls(self) -> FullTextConfig:
        expected = {
            "europe_pmc_base_url": "https://www.ebi.ac.uk/europepmc/webservices/rest",
            "istex_base_url": "https://api.istex.fr",
            "core_base_url": "https://api.core.ac.uk/v3",
            "hal_base_url": "https://api.archives-ouvertes.fr/search",
            "semantic_scholar_base_url": ("https://api.semanticscholar.org/graph/v1"),
            "openalex_base_url": "https://api.openalex.org",
            "unpaywall_base_url": "https://api.unpaywall.org/v2",
            "doaj_base_url": "https://doaj.org/api/search/articles",
            "crossref_base_url": "https://api.crossref.org",
            "elsevier_article_base_url": "https://api.elsevier.com/content/article/doi",
        }
        for name, required in expected.items():
            if getattr(self, name).rstrip("/") != required:
                raise ValueError(f"full_text.{name} must use {required}")
            setattr(self, name, required)
        return self


class PublisherProfile(BaseModel):
    """Trusted browser selectors and domains for one authorized publisher route."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    label: str = Field(min_length=1, max_length=120)
    login_url: str
    allowed_domains: list[str] = Field(min_length=1, max_length=30)
    username_selector: str = Field(min_length=1, max_length=500)
    password_selector: str = Field(min_length=1, max_length=500)
    submit_selector: str = Field(min_length=1, max_length=500)
    success_selector: str = Field(min_length=1, max_length=500)
    article_ready_selector: str | None = Field(default=None, max_length=500)
    pdf_link_selectors: list[str] = Field(default_factory=list, max_length=20)
    full_text_selector: str | None = Field(default=None, max_length=500)

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().casefold().lstrip(".")
            if not re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
                domain,
            ):
                raise ValueError("publisher allowed domain is invalid")
            normalized.append(domain)
        return list(dict.fromkeys(normalized))

    @field_validator("login_url")
    @classmethod
    def validate_login_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("publisher login_url must be an HTTPS URL without credentials")
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> PublisherProfile:
        hostname = (urlsplit(self.login_url).hostname or "").casefold()
        if not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_domains
        ):
            raise ValueError("publisher login_url must belong to an allowed domain")
        if not self.pdf_link_selectors and not self.full_text_selector:
            raise ValueError("publisher profile needs a PDF link or full-text selector")
        return self


class PublisherAccessConfig(BaseModel):
    """Explicit opt-in browser automation for contractually authorized full text."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    username_env: str = Field(
        default="CIDERSCHOLAR_LDAP_USERNAME",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    password_env: str = Field(
        default="CIDERSCHOLAR_LDAP_PASSWORD_DPAPI",
        pattern=r"^[A-Z][A-Z0-9_]{2,127}$",
    )
    browser_channel: Literal["msedge", "chrome", "chromium"] = "msedge"
    headless: bool = True
    navigation_timeout_seconds: int = Field(default=60, ge=10, le=180)
    request_delay_seconds: float = Field(default=1.0, ge=0.5, le=30.0)
    max_records_per_run: int = Field(default=500, ge=1, le=1000)
    max_download_bytes: int = Field(default=104_857_600, ge=1_048_576, le=524_288_000)
    profiles: list[PublisherProfile] = Field(default_factory=list, max_length=30)

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, values: list[PublisherProfile]) -> list[PublisherProfile]:
        identifiers = [profile.id for profile in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("publisher profile ids must be unique")
        return values


class EvidenceConfig(BaseModel):
    """Bounded passage selection and per-article extraction."""

    model_config = ConfigDict(extra="forbid")

    min_passages_per_article: int = Field(default=3, ge=1, le=8)
    passages_per_article: int = Field(default=5, ge=1, le=8)
    max_passages_per_article: int = Field(default=8, ge=1, le=8)
    candidate_chunks_per_article: int = Field(default=100, ge=8, le=500)
    max_passage_characters: int = Field(default=32000, ge=4000, le=42000)
    near_duplicate_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    max_findings_per_article: int = Field(default=8, ge=1, le=20)
    max_output_tokens: int = Field(default=1024, ge=256, le=2048)
    invalid_json_retries: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_passage_counts(self) -> EvidenceConfig:
        if not (
            self.min_passages_per_article
            <= self.passages_per_article
            <= self.max_passages_per_article
        ):
            raise ValueError("evidence passage counts must satisfy min <= default <= max")
        return self


class SynthesisConfig(BaseModel):
    """Bounded hierarchical synthesis from already validated evidence cards."""

    model_config = ConfigDict(extra="forbid")

    max_articles: int = Field(default=20, ge=1, le=20)
    max_themes: int = Field(default=6, ge=1, le=10)
    max_evidence_items: int = Field(default=80, ge=1, le=160)
    max_evidence_per_theme: int = Field(default=24, ge=1, le=80)
    max_excerpt_characters: int = Field(default=800, ge=100, le=2000)
    max_statement_input_characters: int = Field(default=600, ge=100, le=1000)
    max_statements_per_section: int = Field(default=8, ge=1, le=20)
    final_statements_per_theme_section: int = Field(default=2, ge=1, le=4)
    max_output_tokens: int = Field(default=2048, ge=512, le=4096)
    invalid_json_retries: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_evidence_window(self) -> SynthesisConfig:
        if self.max_evidence_items < self.max_articles:
            raise ValueError(
                "synthesis.max_evidence_items must cover at least one item per article"
            )
        if self.max_evidence_per_theme < self.max_articles:
            raise ValueError("synthesis.max_evidence_per_theme must cover one item per article")
        return self


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["custom", "8gb", "16gb"] = "custom"
    warning_used_gb: float = Field(default=13.0, gt=0)
    hard_process_limit_gb: float = Field(default=14.0, gt=0)
    minimum_available_mb: int = Field(default=512, ge=128)


class CorpusDistributionConfig(BaseModel):
    """Local filesystem handoff to the user's OneDrive synchronization client."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    synchronized_root: Path | None = None
    administrator_archive_root: Path | None = None
    signature_required: bool = False
    allowed_signers_path: Path | None = None
    expected_folder_name: str = Field(default="CiderScholar", min_length=1, max_length=100)
    check_interval_hours: int = Field(default=24, ge=24, le=168)

    @model_validator(mode="after")
    def require_local_root_when_enabled(self) -> CorpusDistributionConfig:
        if self.enabled and self.synchronized_root is None:
            raise ValueError("distribution.synchronized_root is required when enabled")
        if self.signature_required and self.allowed_signers_path is None:
            raise ValueError("distribution.allowed_signers_path is required for package signatures")
        return self


class SuggestionConfig(BaseModel):
    """Conservative limits for explicit document suggestions."""

    model_config = ConfigDict(extra="forbid")

    maximum_pdf_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    maximum_context_characters: int = Field(default=8000, ge=500, le=16000)
    acceptance_threshold: float = Field(default=0.80, ge=0.5, le=1.0)


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppConfig = Field(default_factory=AppConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    deep_research: DeepResearchConfig = Field(default_factory=DeepResearchConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    article_ranking: ArticleRankingConfig = Field(default_factory=ArticleRankingConfig)
    argo: ArgoConfig = Field(default_factory=ArgoConfig)
    figure_analysis: FigureAnalysisConfig = Field(default_factory=FigureAnalysisConfig)
    bibliographic: BibliographicConfig = Field(default_factory=BibliographicConfig)
    harvest: HarvestConfig = Field(default_factory=HarvestConfig)
    full_text: FullTextConfig = Field(default_factory=FullTextConfig)
    publisher_access: PublisherAccessConfig = Field(default_factory=PublisherAccessConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    synthesis: SynthesisConfig = Field(default_factory=SynthesisConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    distribution: CorpusDistributionConfig = Field(default_factory=CorpusDistributionConfig)
    suggestions: SuggestionConfig = Field(default_factory=SuggestionConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)

    @model_validator(mode="after")
    def enforce_local_models(self) -> Settings:
        if not self.embeddings.local_files_only:
            raise ValueError("runtime embedding models must be local-only")
        if not self.reranker.local_files_only or self.reranker.trust_remote_code:
            raise ValueError("runtime reranker models must be local-only without remote code")
        if self.app.offline_mode:
            raise ValueError("app.offline_mode must be false because ARGO is required")
        if self.bibliographic.enabled and not self.app.allow_bibliographic_apis:
            raise ValueError(
                "app.allow_bibliographic_apis must be true when bibliographic APIs are enabled"
            )
        if self.harvest.enabled and not self.bibliographic.enabled:
            raise ValueError("bibliographic.enabled must be true when harvesting is enabled")
        if self.full_text.enabled and not self.app.allow_bibliographic_apis:
            raise ValueError(
                "app.allow_bibliographic_apis must be true when full-text acquisition is enabled"
            )
        if self.publisher_access.enabled and not self.app.allow_publisher_automation:
            raise ValueError(
                "app.allow_publisher_automation must be true when publisher access is enabled"
            )
        if self.publisher_access.enabled and not self.publisher_access.profiles:
            raise ValueError("at least one publisher profile is required when enabled")
        if self.synthesis.max_output_tokens > self.argo.max_output_tokens:
            raise ValueError(
                "synthesis.max_output_tokens cannot exceed the active LLM output limit"
            )
        return self


def configured_secret_names(settings: Settings) -> set[str]:
    """Return only the environment variable names used by active connectors."""

    names: set[str] = set()
    names.add(settings.argo.api_key_env)
    if settings.publisher_access.enabled:
        names.update(
            {
                settings.publisher_access.username_env,
                settings.publisher_access.password_env,
            }
        )
    if settings.full_text.enabled:
        names.update(
            {
                settings.full_text.istex_token_env,
                settings.full_text.core_api_key_env,
                settings.full_text.semantic_scholar_api_key_env,
                settings.full_text.openalex_api_key_env,
                settings.full_text.elsevier_api_key_env,
            }
        )
    return names


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load YAML configuration and resolve paths from one explicit runtime root.

    On Windows, an existing desktop configuration in LOCALAPPDATA is the
    canonical default. This keeps operational scripts aligned with the
    installed application instead of silently creating a second RAG beside the
    source tree.
    """

    if config_path is None:
        configured = os.environ.get("CIDERSCHOLAR_CONFIG_PATH", "").strip()
        if configured:
            config_file = Path(configured).resolve()
            project_dir = config_file.parent
        else:
            local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
            desktop_candidate = (
                Path(local_app_data).resolve() / "CiderScholar" / "UserData" / "config.yaml"
                if local_app_data
                else None
            )
            if desktop_candidate is not None and desktop_candidate.is_file():
                config_file = desktop_candidate
                project_dir = config_file.parent
            else:
                project_dir = Path(__file__).resolve().parents[1]
                candidate = project_dir / "config.yaml"
                config_file = candidate if candidate.exists() else None
    else:
        config_file = Path(config_path).resolve()
        project_dir = config_file.parent

    raw: dict[str, object] = {}
    if config_file is not None:
        if not config_file.is_file():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        with config_file.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        user_override = config_file.with_name("config.user.yaml")
        if user_override.is_file():
            with user_override.open("r", encoding="utf-8") as handle:
                override = yaml.safe_load(handle) or {}
            if not isinstance(override, dict):
                raise ValueError("config.user.yaml must contain a mapping")
            raw = _deep_merge(raw, override)

    settings = Settings.model_validate(raw)
    settings.paths = settings.paths.resolved(project_dir)
    synchronized_root = settings.distribution.synchronized_root
    if synchronized_root is not None and not synchronized_root.is_absolute():
        settings.distribution.synchronized_root = (project_dir / synchronized_root).resolve()
    archive_root = settings.distribution.administrator_archive_root
    if archive_root is not None and not archive_root.is_absolute():
        settings.distribution.administrator_archive_root = (project_dir / archive_root).resolve()
    allowed_signers = settings.distribution.allowed_signers_path
    if allowed_signers is not None and not allowed_signers.is_absolute():
        settings.distribution.allowed_signers_path = (project_dir / allowed_signers).resolve()
    from app.secrets import hydrate_user_environment

    hydrate_user_environment(configured_secret_names(settings))
    return settings


def _deep_merge(
    base: dict[str, object],
    override: dict[str, object],
) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged
