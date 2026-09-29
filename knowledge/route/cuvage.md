---
schema_version: 1
id: "route.cuvage"
revision: 1
kind: "route"
title: "Routage candidat du cuvage"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"3f830672cd189b1d65751a2cc3502da6705207989537dff50c0bbab173e43969"}]
depends_on: ["gateway.cider_domain","taxonomy.cuvage","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":90,"match":{"any_terms":["cuvage","macération de la pulpe","macération avant pressurage","mash maceration","pre-press maceration"]},"exclude":{"any_terms":["macération carbonique","carbonic maceration"]},"target_ids":["taxonomy.cuvage","recipe.chat_research"]}
---
