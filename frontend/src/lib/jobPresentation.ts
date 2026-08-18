import type { JobState, JobStep } from "@/types/api";

export const jobStateLabels: Record<JobState, string> = {
  queued: "En attente",
  running: "En cours",
  succeeded: "Terminé",
  failed: "Échec",
  cancel_requested: "Annulation demandée",
  cancelled: "Annulé",
};

export const jobStepLabels: Record<JobStep, string> = {
  waiting: "Préparation",
  planning: "Analyse et planification de la question",
  search: "Recherche locale dans le corpus",
  enrichment: "Enrichissement bibliographique",
  reranking: "Classement et fusion des passages",
  evidence_selection: "Sélection sémantique des preuves",
  coverage: "Contrôle et complément de la couverture",
  figure_analysis: "Analyse locale des figures",
  generation: "Génération de la réponse finale",
  argo: "Traitement ARGO (ancien suivi)",
  validation: "Validation scientifique",
  persistence: "Enregistrement",
  backup: "Sauvegarde du corpus",
  suggestions: "Étape historique retirée",
  harvest: "Collecte bibliographique",
  index: "Indexation et contrôles",
  publish: "Publication du corpus",
  evidence: "Extraction des preuves",
  verification: "Vérification des affirmations",
  synthesis: "Synthèse approfondie",
  ingestion: "Ingestion des documents",
};
