---
schema_version: 1
id: "policy.relevance_grades"
revision: 1
kind: "method_policy"
title: "Pertinence et contradictions"
language: "multilingual"
authority: "accepted_user_method"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§5.1, point 6","revision_sha256":"355c44d3a1d0b3496f1fa55c59ecc484054ad3656c37aafa1a0ca94ef926ba03"}]
depends_on: []
stage: "semantic_filter"
required: true
data: {"instruction":"Évaluer les candidats A à D par rapport à toute la question et aux besoins de vérification. Une contradiction directement pertinente reste A. Ce filtre ne mesure pas la couverture et ne déclenche aucune nouvelle recherche.","applies_to":["chatbot"]}
---
