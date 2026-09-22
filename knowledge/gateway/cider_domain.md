---
schema_version: 1
id: "gateway.cider_domain"
revision: 1
kind: "gateway"
title: "Contexte cidricole explicite"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"d1902afa3e5f0b59446bed54a01d37fcbf5ad6c798bd57ef164fde953571cf51"}]
depends_on: []
stage: "routing"
required: false
data: {"criteria":{"any_terms":["cidre","cidricole","cider","pomme","pommes","apple","apples"]},"on_uncertain":"record_ambiguity"}
---
