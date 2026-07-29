import { useEffect, useState } from "react";

import { AlertTriangle, CheckCircle2, Clock3, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/Button";
import type { DurableJob, JobState, JobStep } from "@/types/api";

import { formatJobDuration, formatRetryTime } from "./jobStatus";
import { isTerminalJob } from "./jobPolling";

const stateLabels: Record<JobState, string> = {
  queued: "En attente",
  running: "En cours",
  succeeded: "Terminé",
  failed: "Échec",
  cancel_requested: "Annulation demandée",
  cancelled: "Annulé",
};

const stepLabels: Record<JobStep, string> = {
  waiting: "Préparation",
  search: "Recherche locale",
  reranking: "Reranking des passages",
  enrichment: "Enrichissement bibliographique",
  argo: "Génération de la réponse",
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
  ingestion: "Ingestion des documents privés",
};

interface JobStatusCardProps {
  job: DurableJob;
  onCancel?: (job: DurableJob) => Promise<void>;
  onRetry?: (job: DurableJob) => Promise<void>;
}

export function JobStatusCard({ job, onCancel, onRetry }: JobStatusCardProps) {
  const [nowMilliseconds, setNowMilliseconds] = useState(() => Date.now());
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const terminal = isTerminalJob(job);

  useEffect(() => {
    if (terminal) return;
    const intervalId = window.setInterval(() => setNowMilliseconds(Date.now()), 1_000);
    return () => window.clearInterval(intervalId);
  }, [terminal]);

  const retry = async () => {
    if (!onRetry || retrying) return;
    setRetrying(true);
    try {
      await onRetry(job);
    } finally {
      setRetrying(false);
    }
  };

  const cancel = async () => {
    if (!onCancel || cancelling) return;
    setCancelling(true);
    try {
      await onCancel(job);
    } finally {
      setCancelling(false);
    }
  };

  const durationEnd = terminal ? Date.parse(job.updated_at) : nowMilliseconds;
  const displayedStep =
    job.error?.code === "quota"
      ? "Attente du quota ARGO"
      : job.state === "queued"
        ? "File d’attente — créneaux de traitement occupés"
        : stepLabels[job.step];
  const statusIcon =
    job.state === "failed" ? (
      <AlertTriangle aria-hidden="true" className="size-4" />
    ) : job.state === "succeeded" ? (
      <CheckCircle2 aria-hidden="true" className="size-4" />
    ) : (
      <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
    );

  return (
    <section
      aria-label={`Travail de réponse : ${stateLabels[job.state]}`}
      aria-live="polite"
      className="ml-11 max-w-xl rounded-2xl border border-forest-200 bg-forest-50 px-4 py-3 text-sm text-forest-900 shadow-soft"
      role="status"
    >
      <div className="flex items-center gap-2 font-bold">
        {statusIcon}
        {stateLabels[job.state]}
      </div>
      <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-forest-800">
        <div className="flex gap-1.5">
          <dt className="font-semibold">Étape :</dt>
          <dd>{displayedStep}</dd>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock3 aria-hidden="true" className="size-3.5" />
          <dt className="font-semibold">Durée :</dt>
          <dd>{formatJobDuration(job.created_at, durationEnd)}</dd>
        </div>
      </dl>
      {job.error?.code === "quota" && job.error.retry_at && (
        <p className="mt-3 rounded-xl bg-cider-100 px-3 py-2 text-xs font-medium text-cider-900">
          Quota ARGO temporairement atteint. Reprise automatique estimée à{" "}
          <time dateTime={job.error.retry_at}>{formatRetryTime(job.error.retry_at)}</time>.
        </p>
      )}
      {job.state === "queued" && job.error?.code !== "quota" && (
        <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-xs font-medium text-forest-800">
          {job.error?.message ??
            "Les créneaux de traitement actifs sont occupés. Cette réponse démarrera automatiquement dès qu’un créneau se libère, sans consommer de requête ARGO pendant l’attente."}
        </p>
      )}
      {(job.state === "queued" || job.state === "running") && onCancel && (
        <div className="mt-3 border-t border-forest-200 pt-3">
          {job.state === "running" && (
            <p className="mb-2 text-xs text-forest-800">
              L’annulation prendra effet à la prochaine étape sûre.
            </p>
          )}
          <Button
            className="min-h-9 px-3 py-2 text-xs"
            loading={cancelling}
            onClick={cancel}
            variant="secondary"
          >
            {job.state === "queued" ? "Annuler le travail" : "Demander l’annulation"}
          </Button>
        </div>
      )}
      {job.state === "cancel_requested" && (
        <p className="mt-3 border-t border-forest-200 pt-3 text-xs text-forest-800">
          La génération s’arrêtera à la prochaine étape sûre.
        </p>
      )}
      {job.state === "failed" && (
        <div className="mt-3 border-t border-forest-200 pt-3">
          <p className="text-xs text-red-800">{job.error?.message ?? "Le travail a échoué."}</p>
          {onRetry && (
            <Button className="mt-2 min-h-9 px-3 py-2 text-xs" loading={retrying} onClick={retry}>
              Relancer
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
