from __future__ import annotations

import json

from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.updates.pilot_rag import (
    MAX_SCIENTIFIC_GENERATION_REQUESTS,
    CiderAbstractRagService,
)
from app.updates.vector_index import BibliographicHybridResult


def test_production_generation_budget_uses_one_draft_and_one_reviewer_pass() -> None:
    assert MAX_SCIENTIFIC_GENERATION_REQUESTS == 2

    record = BibliographicHybridResult(
        rank=1,
        record_id="11111111-1111-1111-1111-111111111111",
        title="Cider microbiology",
        abstract="Yeasts and bacteria influence cider fermentation.",
        authors=["Ada Test"],
        journal="Cider Science",
        publication_year=2025,
        doi="10.1000/cider",
        url="https://doi.org/10.1000/cider",
        sources=["OpenAlex"],
        lexical_rank=1,
        vector_rank=1,
        score=0.1,
    )

    class Client:
        calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            statement = (
                "- Fragment en liste."
                if self.calls == 1
                else "Les levures influencent la fermentation."
            )
            return GenerationResponse(
                model="chat-gpt-oss-20b",
                content=json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": statement,
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    }
                ),
                done_reason="stop",
                metrics=GenerationMetrics(
                    total_duration_seconds=0.1,
                    load_duration_seconds=0.0,
                    prompt_eval_count=50,
                    prompt_eval_duration_seconds=0.0,
                    eval_count=20,
                    eval_duration_seconds=0.1,
                ),
            )

    client = Client()
    result = CiderAbstractRagService(client).answer("Réponds en prose.", [record])

    assert result.answer_markdown.startswith("Les levures")
    assert client.calls == 2
