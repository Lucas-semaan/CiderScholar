---
schema_version: 1
id: "policy.relevance_grades"
revision: 1
kind: "method_policy"
title: "Pertinence et contradictions"
language: "multilingual"
authority: "accepted_user_method"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§5.1, point 6","revision_sha256":"3f830672cd189b1d65751a2cc3502da6705207989537dff50c0bbab173e43969"}]
depends_on: []
stage: "semantic_filter"
required: true
data: {"instruction":"Évaluer les candidats A à D par rapport à toute la question et aux besoins de vérification. Une contradiction directement pertinente reste A. Ce filtre ne mesure pas la couverture et ne déclenche aucune nouvelle recherche.","applies_to":["chatbot"]}
---
