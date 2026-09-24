---
schema_version: 1
id: "recipe.chat_conversation"
revision: 1
kind: "recipe"
title: "Déclaration du parcours sur preuves déjà disponibles"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§5.1 Pipeline conversationnel","revision_sha256":"05140e5d35c03a8f6347ea5627a08216d87a6aacc299548d447b4ba255d27044"}]
depends_on: ["policy.sqlite_evidence","policy.claim_types"]
stage: "routing"
required: false
data: {"recipe_version":"1.0.0","interaction_mode":"conversation","steps":[{"id":"synthesize","handler":"synthesize_validated","knowledge_ids":["policy.sqlite_evidence","policy.claim_types"],"input_contract":"sqlite_evidence","output_contract":"validated_answer"}]}
---
Déclaration sans exécution. Le futur adaptateur devra vérifier que les preuves SQLite de la conversation restent disponibles et valides.
