"""Admission of every visible scientific chat field through the shared verifier."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.llm.argo_client import ArgoError, ArgoProtocolError
from app.llm.claim_verification import ClaimVerifier, SemanticVerificationError
from app.llm.response_language import question_language
from app.llm.validation_cache import content_key

if TYPE_CHECKING:
    from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
    from app.updates.pilot_rag import CiderEvidenceAnswer


class MandatoryVerificationError(ArgoProtocolError):
    """Technical failure; scientific generation must wait for a successful retry."""


class ChatAnswerVerifier:
    def __init__(self, verifier: ClaimVerifier) -> None:
        self.verifier = verifier
        self.removed_count = 0

    def admit(
        self,
        question: str,
        answer: CiderEvidenceAnswer,
        evidence: dict[str, tuple[ChatEvidenceRecord, ChatEvidencePassage]],
    ) -> CiderEvidenceAnswer:
        fields: list[tuple[str, str, list[str]]] = []
        for index, statement in enumerate(answer.statements):
            fields.append((f"statement:{index}:text", statement.statement, statement.evidence_ids))
            if statement.mechanism:
                fields.append(
                    (f"statement:{index}:mechanism", statement.mechanism, statement.evidence_ids)
                )
        if answer.definition:
            fields.append(("definition", answer.definition, answer.definition_evidence_ids))
        for index, limitation in enumerate(answer.limitations):
            ids = (
                answer.limitation_evidence_ids[index]
                if index < len(answer.limitation_evidence_ids)
                else []
            )
            fields.append((f"limitation:{index}", limitation, ids))
        if answer.insufficiency_message:
            # An abstention may describe missing documentation, never add scientific facts.
            fields.append(("insufficiency", answer.insufficiency_message, []))
        claims = []
        owners: dict[str, set[str]] = {}
        sentences_by_owner: dict[str, list[tuple[str, str]]] = {}
        rejected: set[str] = set()
        for owner, text, ids in fields:
            if not set(ids) <= set(evidence):
                rejected.add(owner)
                continue
            for sentence in re.split(r"(?<=[.!?;])\s+", text):
                sentence = sentence.strip()
                if not sentence:
                    continue
                identifier = "claim-" + content_key([sentence, ids])[:20]
                sentences_by_owner.setdefault(owner, []).append((identifier, sentence))
                if identifier in owners:
                    owners[identifier].add(owner)
                    continue
                owners[identifier] = {owner}
                claims.append(
                    {
                        "claim_id": identifier,
                        "statement": sentence,
                        "role": "result",
                        "verbatim_evidence": [evidence[item][1].text for item in ids],
                        "source_metadata": [
                            {
                                "title": evidence[item][0].title,
                                "grade": evidence[item][0].evidence_grade,
                            }
                            for item in ids
                        ],
                    }
                )
        try:
            checks = self.verifier.verify(question, claims)
        except (ArgoError, SemanticVerificationError) as error:
            raise MandatoryVerificationError("mandatory_claim_verification_incomplete") from error
        supported = {check.claim_id: check.supported for check in checks}
        rejected.update(
            owner for check in checks if not check.supported for owner in owners[check.claim_id]
        )
        self.removed_count += len(rejected)

        statements = []
        for index, item in enumerate(answer.statements):
            text_owner = f"statement:{index}:text"
            mechanism_owner = f"statement:{index}:mechanism"
            supported_sentences = [
                sentence
                for claim_id, sentence in sentences_by_owner.get(text_owner, [])
                if supported.get(claim_id, False)
            ]
            if not supported_sentences:
                continue
            if item.mechanism and mechanism_owner in rejected:
                # A mechanism qualifies the whole result and cannot be detached
                # silently when it fails mandatory verification.
                continue
            if text_owner in rejected:
                item = item.model_copy(update={"statement": " ".join(supported_sentences)})
            statements.append(item)
        limitations = [
            item
            for index, item in enumerate(answer.limitations)
            if f"limitation:{index}" not in rejected
        ]
        update = {"statements": statements, "limitations": limitations}
        if "definition" in rejected:
            update.update(definition=None, definition_evidence_ids=[])
        update["limitation_evidence_ids"] = [
            ids
            for index, ids in enumerate(answer.limitation_evidence_ids)
            if f"limitation:{index}" not in rejected
        ]
        if not statements:
            update.update(
                status="insufficient",
                insufficiency_message=(
                    "Aucune affirmation étayée ne peut être présentée."
                    if question_language(question) == "fr"
                    else "No supported scientific claim can be presented."
                ),
            )
        return answer.model_copy(update=update)
