"""Scientific boundary regressions for the single-wave production pipeline."""

import json
from contextlib import closing

import pytest

from app.chat_effort import AnswerEffort, answer_effort_budget
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.llm.argo_client import ArgoProtocolError, ArgoUnavailableError
from app.llm.chat_claims import ChatAnswerVerifier, MandatoryVerificationError
from app.llm.claim_verification import ClaimVerifier
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.llm.validation_cache import ValidationCache
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.evidence_selection import distinct_evidence, focused_excerpt
from app.retrieval.global_semantic_filter import ArgoGlobalSemanticEvidenceFilter
from app.retrieval.hypothesis_planning import HypotheticalResearchPlan, VerificationNeed
from app.retrieval.rehydration import rehydrate_records
from app.services.workflows import _chat_retrieval_corpus_fingerprint
from app.updates.pilot_rag import CiderEvidenceAnswer, CitedEvidenceStatement


def record(index=1, text="Apple fermentation was studied."):
    return ChatEvidenceRecord(
        record_id=f"common:{index}",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Fermentation study",
        evidence_grade="A",
        passages=[ChatEvidencePassage(evidence_id=f"e{index}", text=text)],
    )


def need(index=1):
    return VerificationNeed(
        need_id=f"v{index}",
        claim_to_verify="Effect on fermentation",
        evidence_required="An original observation",
        search_query=f"fermentation effect {index}",
        contradiction_query=f"fermentation no effect {index}",
    )


def response(value):
    return GenerationResponse(
        model="test",
        content=json.dumps(value),
        done_reason="stop",
        metrics=GenerationMetrics(
            total_duration_seconds=1,
            load_duration_seconds=0,
            prompt_eval_count=1,
            prompt_eval_duration_seconds=0,
            eval_count=1,
            eval_duration_seconds=0,
        ),
    )


class SemanticClient:
    model = "fixture-v1"

    def __init__(self, fail_at=0):
        self.fail_at = fail_at
        self.calls = []

    def chat(self, messages, **options):
        candidates = json.loads(messages[1]["content"])["candidates"]
        self.calls.append(candidates)
        if len(self.calls) == self.fail_at:
            raise ArgoUnavailableError("synthetic timeout")
        return response(
            {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "relevance": "direct",
                        "rationale": "The passage answers the question.",
                        "supported_need_ids": ["v1"],
                    }
                    for item in candidates
                ]
            }
        )


def test_failed_second_batch_resumes_only_missing_evaluations(tmp_path):
    records = [record(index) for index in range(12)]
    first = SemanticClient(fail_at=2)
    with pytest.raises(ArgoUnavailableError):
        ArgoGlobalSemanticEvidenceFilter(first, cache=ValidationCache(tmp_path)).filter_records(
            "Fermentation effects?", [need()], records
        )
    second = SemanticClient()
    result = ArgoGlobalSemanticEvidenceFilter(
        second, cache=ValidationCache(tmp_path)
    ).filter_records("Fermentation effects?", [need()], records)
    assert [len(batch) for batch in second.calls] == [2]
    assert len(result.selected_records(records)) == 12


def test_changed_question_or_source_never_reuses_a_verdict(tmp_path):
    client = SemanticClient()
    gate = ArgoGlobalSemanticEvidenceFilter(client, cache=ValidationCache(tmp_path))
    gate.filter_records("Fermentation effects?", [need()], [record()])
    gate.filter_records("Fermentation effects?", [need()], [record()])
    assert len(client.calls) == 1
    gate.filter_records("Temperature effects?", [need()], [record()])
    gate.filter_records("Temperature effects?", [need()], [record(text="Changed result.")])
    assert len(client.calls) == 3


def test_semantic_input_is_split_by_assembled_characters():
    client = SemanticClient()
    gate = ArgoGlobalSemanticEvidenceFilter(client, max_input_characters=8_192)
    gate.filter_records(
        "Fermentation effects?", [need()], [record(i, "Evidence text. " * 200) for i in range(10)]
    )
    assert len(client.calls) > 1
    assert sum(map(len, client.calls)) == 10


def test_single_oversized_semantic_request_fails_before_network():
    client = SemanticClient()
    with pytest.raises(ArgoProtocolError, match="context_exceeded"):
        ArgoGlobalSemanticEvidenceFilter(client, max_input_characters=100).filter_records(
            "Fermentation effects?", [need()], [record()]
        )
    assert client.calls == []


@pytest.mark.parametrize("effort", list(AnswerEffort))
def test_every_contradiction_survives_full_effort_plan(effort):
    budget = answer_effort_budget(effort)
    needs = [need(i + 1) for i in range(budget.verification_need_limit)]
    plan = HypotheticalResearchPlan(
        interpreted_question="Question originale",
        hypothetical_answer="Hypothèse uniquement dense",
        verification_needs=needs,
    )
    lexical = plan.lexical_queries("Question originale")
    assert all(item.contradiction_query in lexical for item in needs)
    assert plan.hypothetical_answer not in lexical
    assert plan.dense_queries("Question originale") == [
        "Question originale",
        plan.hypothetical_answer,
    ]


def test_redundancy_never_erases_a_negated_result_or_distinct_study():
    original = record(text="Fermentation increased acidity.")
    original.passages += [
        ChatEvidencePassage(evidence_id="duplicate", text=original.passages[0].text),
        ChatEvidencePassage(evidence_id="opposite", text="Fermentation did not increase acidity."),
    ]
    selected, count = distinct_evidence([original, record(2)], "Fermentation acidity")
    assert count == 1
    assert [item.evidence_id for item in selected[0].passages] == ["e1", "opposite"]
    assert len(selected) == 2


def test_focused_excerpt_retains_relevant_tail_verbatim():
    text = "An unrelated introduction. " * 100 + "Fermentation did not increase acidity in cider."
    excerpt = focused_excerpt(text, "cider fermentation acidity", 100)
    assert "did not increase acidity" in excerpt
    assert excerpt in text


class ClaimClient:
    model = "fixture-verifier"

    def __init__(self, dimension="implication"):
        self.calls = 0
        self.dimension = dimension

    def chat(self, messages, **options):
        self.calls += 1
        claims = json.loads(messages[1]["content"])["claims"]
        rows = []
        for claim in claims:
            row = {
                name: {"status": "entailed", "reason": "Synthetic check."}
                for name in (
                    "implication",
                    "negation",
                    "unit",
                    "population",
                    "condition",
                    "temporality",
                )
            }
            if "unsupported" in claim["statement"]:
                row[self.dimension]["status"] = "contradicted"
            row["claim_id"] = claim["claim_id"]
            rows.append(row)
        return response({"verifications": rows})


@pytest.mark.parametrize(
    "dimension", ["implication", "negation", "unit", "population", "condition", "temporality"]
)
def test_valid_citation_does_not_admit_an_unsupported_claim(dimension):
    source = record()
    client = ClaimClient(dimension)
    verifier = ChatAnswerVerifier(ClaimVerifier(client))
    answer = CiderEvidenceAnswer(
        statements=[
            CitedEvidenceStatement(statement="An unsupported claim.", evidence_ids=["e1"]),
            CitedEvidenceStatement(statement="A supported observation.", evidence_ids=["e1"]),
        ],
        limitations=[],
    )
    checked = verifier.admit("Fermentation?", answer, {"e1": (source, source.passages[0])})
    assert [item.statement for item in checked.statements] == ["A supported observation."]
    assert verifier.removed_count == 1


def test_mechanism_introduction_and_limitations_are_also_verified():
    source = record()
    verifier = ChatAnswerVerifier(ClaimVerifier(ClaimClient()))
    answer = CiderEvidenceAnswer(
        definition="An unsupported introduction.",
        definition_evidence_ids=["e1"],
        statements=[
            CitedEvidenceStatement(
                statement="A supported result.",
                evidence_ids=["e1"],
                section="documented_effect",
                mechanism="unsupported mechanism",
            )
        ],
        limitations=["An unsupported limit."],
        limitation_evidence_ids=[["e1"]],
    )
    checked = verifier.admit("Fermentation?", answer, {"e1": (source, source.passages[0])})
    assert checked.definition is None and checked.limitations == [] and checked.statements == []


def test_claim_verification_failure_never_becomes_a_scientific_result():
    class Failing:
        def chat(self, *args, **kwargs):
            raise ArgoUnavailableError("synthetic failure")

    source = record()
    with pytest.raises(MandatoryVerificationError):
        ChatAnswerVerifier(ClaimVerifier(Failing())).admit(
            "Fermentation?",
            CiderEvidenceAnswer(
                statements=[CitedEvidenceStatement(statement="Result.", evidence_ids=["e1"])],
                limitations=[],
            ),
            {"e1": (source, source.passages[0])},
        )


def test_revision_rolls_back_and_covers_metadata_and_exclusions(settings):
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    before = _chat_retrieval_corpus_fingerprint(settings)
    with closing(database.connect()) as connection:
        connection.execute(
            "INSERT INTO articles(id,sha256,title,pdf_path,validation_status,source) VALUES ('1', ?, 'Study', 'test.pdf', 'validated', 'local')",
            ("a" * 64,),
        )
        connection.rollback()
    assert _chat_retrieval_corpus_fingerprint(settings) == before
    with closing(database.connect()) as connection, connection:
        connection.execute(
            "INSERT INTO articles(id,sha256,title,pdf_path,validation_status,source) VALUES ('1', ?, 'Study', 'test.pdf', 'validated', 'local')",
            ("a" * 64,),
        )
    inserted = _chat_retrieval_corpus_fingerprint(settings)
    assert inserted != before
    with closing(database.connect()) as connection, connection:
        connection.execute(
            "UPDATE articles SET abstract='Current original abstract', authors='[\"Current author\"]' WHERE id='1'"
        )
    assert _chat_retrieval_corpus_fingerprint(settings) != inserted
    source = record(text="Stale cached text")
    hydrated = rehydrate_records(settings, [source])[0]
    assert hydrated.passages[0].text == "Current original abstract"
    assert hydrated.authors == ["Current author"]
    with closing(database.connect()) as connection, connection:
        connection.execute(
            "INSERT INTO article_retrieval_exclusions(article_id,reason) VALUES ('1','Excluded explicitly')"
        )
    assert rehydrate_records(settings, [source]) == []
