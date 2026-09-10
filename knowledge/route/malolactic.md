---
schema_version: 1
id: "route.malolactic"
revision: 1
kind: "route"
title: "Routage candidat des sigles malolactiques"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"d7cf56d644942651bccd92184d60a061ffde8901cee0c1cd7b2939c2a3ee9d27"}]
depends_on: ["gateway.cider_domain","taxonomy.malolactic_fermentation","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":100,"match":{"any_terms":["FML","TML","MLF","fermentation malolactique","malolactic fermentation"]},"exclude":{},"target_ids":["taxonomy.malolactic_fermentation","recipe.chat_research"]}
---

