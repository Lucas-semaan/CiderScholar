from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppConfig, PathConfig, Settings, load_settings


def test_offline_mode_forbids_bibliographic_network() -> None:
    with pytest.raises(ValidationError):
        AppConfig(offline_mode=True, allow_bibliographic_apis=True)


def test_chat_worker_concurrency_matches_the_argo_minute_capacity() -> None:
    assert AppConfig().chat_worker_concurrency == 20
    with pytest.raises(ValidationError):
        AppConfig(chat_worker_concurrency=21)


def test_paths_cannot_escape_data_directory(tmp_path: Path) -> None:
    paths = PathConfig(data_dir=Path("data"), pdf_dir=Path("../outside"))
    with pytest.raises(ValueError, match="must remain inside"):
        paths.resolved(tmp_path)


def test_retrieval_weights_are_validated() -> None:
    with pytest.raises(ValidationError, match="weights must add up"):
        Settings.model_validate(
            {
                "retrieval": {
                    "lexical_weight": 0.5,
                    "vector_weight": 0.5,
                    "reranker_weight": 0.5,
                }
            }
        )


def test_optional_reranker_stays_disabled_until_scientific_promotion() -> None:
    reranker = Settings().reranker
    assert reranker.enabled is False
    assert reranker.local_files_only is True
    assert reranker.trust_remote_code is False


def test_distribution_uses_only_a_local_synchronized_path(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "distribution:\n  enabled: true\n  synchronized_root: OneDrive/CiderScholar\n",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert (
        settings.distribution.synchronized_root
        == (tmp_path / "OneDrive" / "CiderScholar").resolve()
    )
    assert "graph" not in settings.distribution.model_dump(mode="json")
    with pytest.raises(ValidationError, match="synchronized_root"):
        Settings.model_validate({"distribution": {"enabled": True}})


def test_article_ranking_weights_are_validated() -> None:
    with pytest.raises(ValidationError, match="article ranking weights must add up"):
        Settings.model_validate({"article_ranking": {"best_fragment_weight": 0.9}})


def test_runtime_model_download_cannot_be_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"embeddings": {"local_files_only": False}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"reranker": {"local_files_only": False}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"reranker": {"trust_remote_code": True}})


def test_deep_research_cascade_limits_are_bounded_and_ordered() -> None:
    limits = Settings().deep_research
    assert (
        limits.rrf_candidate_limit,
        limits.cross_encoder_candidate_limit,
        limits.retained_fragment_limit,
    ) == (80, 40, 12)
    with pytest.raises(ValidationError, match="RRF candidate"):
        Settings.model_validate(
            {
                "deep_research": {
                    "rrf_candidate_limit": 20,
                    "cross_encoder_candidate_limit": 40,
                }
            }
        )
    with pytest.raises(ValidationError, match="cross-encoder limit"):
        Settings.model_validate(
            {
                "deep_research": {
                    "cross_encoder_candidate_limit": 10,
                    "retained_fragment_limit": 12,
                }
            }
        )
    with pytest.raises(ValidationError, match="pinned CiderQA calibration"):
        Settings.model_validate({"deep_research": {"contextual_summary_enabled": True}})


def test_qdrant_collection_name_cannot_contain_a_path() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"qdrant": {"collection_name": "../outside"}})


def test_argo_requires_online_mode_and_official_endpoint() -> None:
    with pytest.raises(ValidationError, match="offline_mode must be false"):
        Settings.model_validate({"app": {"offline_mode": True}})

    with pytest.raises(ValidationError, match="official"):
        Settings.model_validate(
            {
                "argo": {"base_url": "https://example.org/api"},
            }
        )


def test_argo_profile_keeps_bibliographic_apis_disabled() -> None:
    settings = Settings.model_validate(
        {
            "app": {"offline_mode": False},
        }
    )

    assert settings.argo.model == "chat-gpt-oss-120b"
    assert settings.argo.api_key_env == "LOCAL_SCIENCE_RAG_ARGO_API_KEY"
    assert settings.app.allow_bibliographic_apis is False


def test_bibliographic_apis_require_explicit_enablement_and_official_urls() -> None:
    with pytest.raises(ValidationError, match="allow_bibliographic_apis"):
        Settings.model_validate({"bibliographic": {"enabled": True}})

    with pytest.raises(ValidationError, match="openalex_base_url"):
        Settings.model_validate(
            {
                "app": {
                    "offline_mode": False,
                    "allow_bibliographic_apis": True,
                },
                "bibliographic": {
                    "enabled": True,
                    "openalex_base_url": "https://example.org",
                },
            }
        )


def test_bibliographic_sources_are_deduplicated() -> None:
    settings = Settings.model_validate(
        {
            "app": {
                "offline_mode": False,
                "allow_bibliographic_apis": True,
            },
            "bibliographic": {
                "enabled": True,
                "sources": ["crossref", "crossref", "europe_pmc"],
            },
        }
    )

    assert settings.bibliographic.sources == ["crossref", "europe_pmc"]


def test_publisher_automation_requires_network_opt_in_and_profile() -> None:
    with pytest.raises(ValidationError, match="allow_publisher_automation"):
        Settings.model_validate({"publisher_access": {"enabled": True}})

    with pytest.raises(ValidationError, match="at least one publisher profile"):
        Settings.model_validate(
            {
                "app": {
                    "offline_mode": False,
                    "allow_publisher_automation": True,
                },
                "publisher_access": {"enabled": True},
            }
        )


def test_clarivate_expanded_mode_requires_its_official_endpoint() -> None:
    base = {
        "app": {
            "offline_mode": False,
            "allow_bibliographic_apis": True,
        },
        "bibliographic": {
            "enabled": True,
            "sources": ["clarivate"],
            "clarivate_api_mode": "expanded",
        },
    }
    with pytest.raises(ValidationError, match="clarivate_api_mode=expanded"):
        Settings.model_validate(base)

    base["bibliographic"]["clarivate_base_url"] = "https://wos-api.clarivate.com/api/wos"
    settings = Settings.model_validate(base)
    assert settings.bibliographic.clarivate_api_mode == "expanded"


def test_evidence_passage_bounds_are_validated() -> None:
    with pytest.raises(ValidationError, match="min <= default <= max"):
        Settings.model_validate(
            {
                "evidence": {
                    "min_passages_per_article": 6,
                    "passages_per_article": 5,
                }
            }
        )


def test_synthesis_windows_and_output_are_bounded() -> None:
    with pytest.raises(ValidationError, match="one item per article"):
        Settings.model_validate(
            {
                "synthesis": {
                    "max_articles": 20,
                    "max_evidence_items": 10,
                }
            }
        )
    with pytest.raises(ValidationError, match="active LLM output limit"):
        Settings.model_validate(
            {
                "argo": {"max_output_tokens": 1024},
                "synthesis": {"max_output_tokens": 2048},
            }
        )
