import type { ReadinessReport } from "@/types/api";

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
