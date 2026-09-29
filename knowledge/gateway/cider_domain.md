---
schema_version: 1
id: "gateway.cider_domain"
revision: 1
kind: "gateway"
title: "Contexte cidricole explicite"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"3f830672cd189b1d65751a2cc3502da6705207989537dff50c0bbab173e43969"}]
depends_on: []
stage: "routing"
required: false
data: {"criteria":{"any_terms":["cidre","cidricole","cider","pomme","pommes","apple","apples"]},"on_uncertain":"record_ambiguity"}
---
