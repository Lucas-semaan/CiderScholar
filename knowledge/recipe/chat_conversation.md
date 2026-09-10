---
schema_version: 1
id: "recipe.chat_conversation"
revision: 1
kind: "recipe"
title: "Déclaration du parcours sur preuves déjà disponibles"
language: "multilingual"
authority: "proposal"
provenance: [{"document":"docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md","section":"§5.1 Pipeline conversationnel","revision_sha256":"d7cf56d644942651bccd92184d60a061ffde8901cee0c1cd7b2939c2a3ee9d27"}]
depends_on: ["policy.sqlite_evidence","policy.claim_types"]
stage: "routing"
required: false
data: {"recipe_version":"1.0.0","interaction_mode":"conversation","steps":[{"id":"synthesize","handler":"synthesize_validated","knowledge_ids":["policy.sqlite_evidence","policy.claim_types"],"input_contract":"sqlite_evidence","output_contract":"validated_answer"}]}
---
Déclaration sans exécution. Le futur adaptateur devra vérifier que les preuves SQLite de la conversation restent disponibles et valides.
