---
schema_version: 1
id: "recipe.chat_research"
revision: 1
kind: "recipe"
title: "Déclaration du parcours de recherche existant"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§5.1 Pipeline conversationnel","revision_sha256":"355c44d3a1d0b3496f1fa55c59ecc484054ad3656c37aafa1a0ca94ef926ba03"}]
depends_on: ["policy.hypothesis_scope","policy.relevance_grades","policy.sqlite_evidence","policy.claim_types"]
stage: "routing"
required: false
data: {"recipe_version":"1.0.0","interaction_mode":"research","steps":[{"id":"plan","handler":"plan_hypothesis","knowledge_ids":["policy.hypothesis_scope"],"input_contract":"question","output_contract":"hypothesis_plan"},{"id":"retrieve","handler":"retrieve_grouped","knowledge_ids":[],"input_contract":"hypothesis_plan","output_contract":"sqlite_candidates"},{"id":"select","handler":"select_global_evidence","knowledge_ids":["policy.relevance_grades"],"input_contract":"sqlite_candidates","output_contract":"sqlite_evidence"},{"id":"synthesize","handler":"synthesize_validated","knowledge_ids":["policy.sqlite_evidence","policy.claim_types"],"input_contract":"sqlite_evidence","output_contract":"validated_answer"}]}
---
