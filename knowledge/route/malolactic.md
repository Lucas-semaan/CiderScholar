---
schema_version: 1
id: "route.malolactic"
revision: 1
kind: "route"
title: "Routage candidat des sigles malolactiques"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"3f830672cd189b1d65751a2cc3502da6705207989537dff50c0bbab173e43969"}]
depends_on: ["gateway.cider_domain","taxonomy.malolactic_fermentation","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":100,"match":{"any_terms":["FML","TML","MLF","fermentation malolactique","malolactic fermentation"]},"exclude":{},"target_ids":["taxonomy.malolactic_fermentation","recipe.chat_research"]}
---
