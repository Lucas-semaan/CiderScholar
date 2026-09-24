---
schema_version: 1
id: "route.cuvage"
revision: 1
kind: "route"
title: "Routage candidat du cuvage"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"05140e5d35c03a8f6347ea5627a08216d87a6aacc299548d447b4ba255d27044"}]
depends_on: ["gateway.cider_domain","taxonomy.cuvage","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":90,"match":{"any_terms":["cuvage","macération de la pulpe","macération avant pressurage","mash maceration","pre-press maceration"]},"exclude":{"any_terms":["macération carbonique","carbonic maceration"]},"target_ids":["taxonomy.cuvage","recipe.chat_research"]}
---
