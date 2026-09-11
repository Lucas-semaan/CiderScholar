---
schema_version: 1
id: "route.malolactic"
revision: 1
kind: "route"
title: "Routage candidat des sigles malolactiques"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§4 Rechercher et utiliser le RAG","revision_sha256":"355c44d3a1d0b3496f1fa55c59ecc484054ad3656c37aafa1a0ca94ef926ba03"}]
depends_on: ["gateway.cider_domain","taxonomy.malolactic_fermentation","recipe.chat_research"]
stage: "routing"
required: false
data: {"priority":100,"match":{"any_terms":["FML","TML","MLF","fermentation malolactique","malolactic fermentation"]},"exclude":{},"target_ids":["taxonomy.malolactic_fermentation","recipe.chat_research"]}
---
