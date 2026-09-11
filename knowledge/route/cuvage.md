---
schema_version: 1
id: "route.cuvage"
revision: 1
kind: "route"
title: "Routage candidat du cuvage"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"355c44d3a1d0b3496f1fa55c59ecc484054ad3656c37aafa1a0ca94ef926ba03"}]
depends_on: ["gateway.cider_domain","taxonomy.cuvage","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":90,"match":{"any_terms":["cuvage","macération de la pulpe","macération avant pressurage","mash maceration","pre-press maceration"]},"exclude":{"any_terms":["macération carbonique","carbonic maceration"]},"target_ids":["taxonomy.cuvage","recipe.chat_research"]}
---
