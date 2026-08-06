import type {
  DiagnosticWorkerState,
  JobState,
  JobStep,
  JobType,
  ReadinessReport,
} from "@/types/api";

export const diagnosticLabels: Record<keyof ReadinessReport["checks"], string> = {
  argo: "ARGO",
  worker: "Worker durable",
  corpus: "Corpus commun",
  disk: "Espace disque",
};

export function formatQueueAge(seconds: number | null): string {
  if (seconds === null) return "Aucun travail en attente";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours} h ${minutes.toString().padStart(2, "0")}`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "Indisponible";
  if (bytes < 1024) return `${bytes} o`;
  const units = ["Ko", "Mo", "Go", "To"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

export function formatDiagnosticDate(value: string | null): string {
  if (!value) return "Aucun signal reçu";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Horodatage indisponible" : date.toLocaleString("fr-FR");
}

export const workerStatePresentation: Record<
  DiagnosticWorkerState,
  { label: string; tone: "success" | "warning" | "danger"; message: string }
> = {
  healthy: {
    label: "Opérationnel",
    tone: "success",
    message: "Le worker répond et traite les travaux en arrière-plan.",
  },
  stale: {
    label: "Signal ancien",
    tone: "warning",
    message: "Le worker ne donne plus de signal récent. Une requête peut prendre plus de temps.",
  },
};

export const diagnosticJobTypeLabels: Record<JobType, string> = {
  chat_answer: "Réponse scientifique",
  weekly_maintenance: "Maintenance hebdomadaire",
  deep_research: "Recherche approfondie",
  long_synthesis: "Synthèse longue",
  corpus_ingestion: "Ingestion de documents",
};

export const diagnosticJobStateLabels: Record<JobState, string> = {
  queued: "En attente",
  running: "En cours",
  succeeded: "Terminé",
  failed: "Échec",
  cancel_requested: "Annulation demandée",
  cancelled: "Annulé",
};

export const diagnosticJobStepLabels: Record<JobStep, string> = {
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
  suggestions: "Import des suggestions",
  harvest: "Collecte bibliographique",
  index: "Indexation et contrôles",
  publish: "Publication du corpus",
  evidence: "Extraction des preuves",
  verification: "Vérification des affirmations",
  synthesis: "Synthèse approfondie",
  ingestion: "Ingestion des documents",
};
