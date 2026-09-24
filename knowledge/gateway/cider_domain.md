---
schema_version: 1
id: "gateway.cider_domain"
revision: 1
kind: "gateway"
title: "Contexte cidricole explicite"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"05140e5d35c03a8f6347ea5627a08216d87a6aacc299548d447b4ba255d27044"}]
depends_on: []
stage: "routing"
required: false
data: {"criteria":{"any_terms":["cidre","cidricole","cider","pomme","pommes","apple","apples"]},"on_uncertain":"record_ambiguity"}
---
