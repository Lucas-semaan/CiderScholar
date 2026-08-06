from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.corpora import CorpusScope
from app.deep_research.citations import CitationSourceFragment
from app.deep_research.claims import (
    AtomicClaimExtractionError,
    AtomicClaimExtractionStage,
)
from app.jobs.contracts import DeepResearchPayload


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def chat(self, _messages, **_kwargs):
        return SimpleNamespace(content=json.dumps(self.payload))


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Quel effet la température produit-elle ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _fragment() -> CitationSourceFragment:
    return CitationSourceFragment(
        scope=CorpusScope.COMMON,
        article_id="article-claims",
        chunk_id=7,
        page_start=4,
        page_end=4,
        text=(
            "La concentration en esters augmente à 18 °C. "
            "Les auteurs interprètent cette hausse comme un effet de la levure."
        ),
    )


def test_atomic_claim_has_one_role_and_verbatim_local_excerpt(tmp_path) -> None:
    excerpt = "La concentration en esters augmente à 18 °C."
    stage = AtomicClaimExtractionStage(
        _Client(
            {
                "claims": [
                    {
                        "statement": "La concentration en esters augmente à 18 °C.",
                        "role": "result",
                        "evidence": [
                            {
                                "source_key": "source-1",
                                "source_excerpt": excerpt,
                            }
                        ],
                    }
                ]
            }
        ),
        tmp_path,
    )
    payload = _payload()

    checkpoint = stage.extract(payload, [_fragment()])
    claim = checkpoint.claims[0]

    assert claim.role == "result"
    assert len(claim.evidence) == 1
    assert claim.evidence[0].source_excerpt == excerpt
    assert claim.evidence[0].scope is CorpusScope.COMMON
    assert claim.evidence[0].article_id == "article-claims"
    assert claim.evidence[0].chunk_id == 7
    assert stage.load(payload) == checkpoint


def test_non_verbatim_claim_excerpt_is_rejected(tmp_path) -> None:
    stage = AtomicClaimExtractionStage(
        _Client(
            {
                "claims": [
                    {
                        "statement": "Une hausse a été observée.",
                        "role": "result",
                        "evidence": [
                            {
                                "source_key": "source-1",
                                "source_excerpt": "Extrait inventé par le modèle.",
                            }
                        ],
                    }
                ]
            }
        ),
        tmp_path,
    )

    with pytest.raises(AtomicClaimExtractionError, match="supplied local excerpts"):
        stage.extract(_payload(), [_fragment()])


def test_claim_stage_without_argo_persists_safe_empty_checkpoint(tmp_path) -> None:
    stage = AtomicClaimExtractionStage(None, tmp_path)

    checkpoint = stage.extract(_payload(), [_fragment()])

    assert checkpoint.source_fragment_count == 1
    assert checkpoint.claims == []
