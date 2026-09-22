---
schema_version: 1
id: "route.matrix_transfer"
revision: 1
kind: "route"
title: "Routage candidat des questions de transposition"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"d1902afa3e5f0b59446bed54a01d37fcbf5ad6c798bd57ef164fde953571cf51"}]
depends_on: ["gateway.cider_domain","policy.matrix_transfer","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":80,"match":{"any_terms":["transposition","transposer","transférable","transfert","autre matrice","other matrix","transfer"]},"exclude":{},"target_ids":["policy.matrix_transfer","recipe.chat_research"]}
---
