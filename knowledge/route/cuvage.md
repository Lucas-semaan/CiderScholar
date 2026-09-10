---
schema_version: 1
id: "route.cuvage"
revision: 1
kind: "route"
title: "Routage candidat du cuvage"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"d7cf56d644942651bccd92184d60a061ffde8901cee0c1cd7b2939c2a3ee9d27"}]
depends_on: ["gateway.cider_domain","taxonomy.cuvage","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":90,"match":{"any_terms":["cuvage","macération de la pulpe","macération avant pressurage","mash maceration","pre-press maceration"]},"exclude":{"any_terms":["macération carbonique","carbonic maceration"]},"target_ids":["taxonomy.cuvage","recipe.chat_research"]}
---

