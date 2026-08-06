import { useCallback } from "react";

import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Cpu,
  ListChecks,
  RefreshCw,
  ServerCog,
  TriangleAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import { formatJobDuration } from "@/lib/time";

import {
  diagnosticLabels,
  diagnosticJobStateLabels,
  diagnosticJobStepLabels,
  diagnosticJobTypeLabels,
  formatBytes,
  formatDiagnosticDate,
  formatQueueAge,
  workerStatePresentation,
} from "./diagnosticPresentation";

export function DiagnosticPage() {
  const loadReadiness = useCallback(() => api.diagnostics.readiness(), []);
  const { data, error, loading, refresh } = useRemoteData(loadReadiness);
  const loadSystemDiagnostics = useCallback(() => api.system.diagnostics(), []);
  const systemDiagnostics = useRemoteData(loadSystemDiagnostics);

  const refreshAll = () => {
    refresh();
    systemDiagnostics.refresh();
  };

  if (loading && !data) return <LoadingState label="Contrôle du poste…" />;
  if (error && !data) return <ErrorState message={error} retry={refresh} />;
  if (!data) return null;

  return (
    <div className="space-y-8">
      <PageHeader
        actions={
          <Button
            loading={loading || systemDiagnostics.loading}
            onClick={refreshAll}
            variant="secondary"
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            Actualiser les contrôles
          </Button>
        }
        description="Vérifie les services nécessaires sans générer de texte scientifique ni exposer le contenu de la file."
        eyebrow="Préparation"
        title="Diagnostic de démonstration"
      />

      {error && <ErrorState message={error} retry={refresh} />}

      <SystemDiagnosticsPanel
        error={systemDiagnostics.error}
        loading={systemDiagnostics.loading}
        report={systemDiagnostics.data}
        retry={systemDiagnostics.refresh}
      />

      <section
        aria-live="polite"
        className={
          data.ready
            ? "rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-emerald-900"
            : "rounded-2xl border border-amber-200 bg-amber-50 p-5 text-amber-950"
        }
        role="status"
      >
        <div className="flex items-start gap-3">
          {data.ready ? (
            <CheckCircle2 aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
          ) : (
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
          )}
          <div>
            <p className="font-bold">
              {data.ready ? "Le poste est prêt" : "Une action est requise avant la démonstration"}
            </p>
            <p className="mt-1 text-sm opacity-80">
              Dernier contrôle : {new Date(data.checked_at).toLocaleString("fr-FR")}
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        {Object.entries(data.checks).map(([key, check]) => {
          const name = key as keyof typeof data.checks;
          const ready = check.state === "ready";
          return (
            <Card key={name}>
              <CardHeader className="flex items-center justify-between gap-3">
                <p className="font-bold text-slate-900">{diagnosticLabels[name]}</p>
                <Badge tone={ready ? "success" : "warning"}>{ready ? "Prêt" : "À corriger"}</Badge>
              </CardHeader>
              <CardBody>
                <p className="text-sm font-semibold text-slate-800">{check.message}</p>
                <p className="mt-3 text-sm leading-6 text-slate-500">{check.action}</p>
              </CardBody>
            </Card>
          );
        })}
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <ListChecks aria-hidden="true" className="size-5 text-forest-600" />
            <div>
              <p className="font-bold text-slate-900">File de travaux</p>
              <p className="mt-1 text-xs text-slate-500">
                Mesures techniques sans question ni réponse
              </p>
            </div>
          </div>
        </CardHeader>
        <CardBody className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <QueueMetric label="Profondeur" value={data.queue.depth.toString()} />
          <QueueMetric label="En attente" value={data.queue.queued.toString()} />
          <QueueMetric label="En cours" value={data.queue.running.toString()} />
          <QueueMetric label="Annulation" value={data.queue.cancel_requested.toString()} />
          <QueueMetric
            icon
            label="Plus ancien"
            value={formatQueueAge(data.queue.oldest_age_seconds)}
          />
        </CardBody>
      </Card>
    </div>
  );
}

function SystemDiagnosticsPanel({
  report,
  loading,
  error,
  retry,
}: {
  report: Awaited<ReturnType<typeof api.system.diagnostics>> | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
}) {
  if (loading && !report) return <LoadingState label="Lecture de l’état technique…" />;
  if (error && !report) {
    return (
      <Card>
        <CardBody>
          <ErrorState message={error} retry={retry} />
        </CardBody>
      </Card>
    );
  }
  if (!report) return null;

  const worker = workerStatePresentation[report.worker.state];
  return (
    <section aria-labelledby="runtime-diagnostics-title" className="space-y-4">
      <div>
        <h2 className="text-lg font-bold text-slate-900" id="runtime-diagnostics-title">
          État de traitement
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Indicateurs locaux pour distinguer une requête lente d’un worker bloqué.
        </p>
      </div>
      {error && <ErrorState message={error} retry={retry} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <ServerCog aria-hidden="true" className="size-5 text-forest-600" />
              <p className="font-bold text-slate-900">Worker</p>
            </div>
            <Badge tone={worker.tone}>{worker.label}</Badge>
          </CardHeader>
          <CardBody>
            <p className="text-sm leading-6 text-slate-600">{worker.message}</p>
            <p className="mt-3 text-xs text-slate-500">
              Dernier signal : {formatDiagnosticDate(report.worker.heartbeat_at)}
            </p>
          </CardBody>
        </Card>
        <RuntimeMetric
          icon={ListChecks}
          label="Travaux actifs"
          value={report.active_jobs.length.toString()}
        />
        <RuntimeMetric
          icon={Cpu}
          label="Mémoire du worker"
          value={formatBytes(report.process.worker_rss_bytes)}
        />
        <RuntimeMetric
          icon={Cpu}
          label="Mémoire de l’API"
          value={formatBytes(report.process.api_rss_bytes)}
        />
      </div>
      <Card>
        <CardHeader>
          <p className="font-bold text-slate-900">Mémoire de la machine</p>
        </CardHeader>
        <CardBody>
          <p className="text-2xl font-bold tracking-[-0.02em] text-slate-900">
            {formatBytes(report.process.system_available_bytes)} disponibles
          </p>
          <p className="mt-1 text-sm text-slate-500">Mémoire disponible au moment du contrôle.</p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <p className="font-bold text-slate-900">Travaux en cours</p>
        </CardHeader>
        <CardBody>
          {report.active_jobs.length === 0 ? (
            <p className="text-sm text-slate-500">Aucun travail actif.</p>
          ) : (
            <ul className="divide-y divide-slate-100" aria-label="Travaux actifs">
              {report.active_jobs.map((job) => (
                <li
                  className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                  key={job.id}
                >
                  <div>
                    <p className="text-sm font-semibold text-slate-800">
                      {diagnosticJobTypeLabels[job.type]}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Étape : {diagnosticJobStepLabels[job.step]} · En cours depuis{" "}
                      {formatJobDuration(job.created_at, Date.now())}
                    </p>
                  </div>
                  <Badge tone={job.state === "running" ? "info" : "neutral"}>
                    {diagnosticJobStateLabels[job.state]}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      {report.warnings.length > 0 && (
        <section aria-labelledby="diagnostic-warnings-title">
          <h3 className="sr-only" id="diagnostic-warnings-title">
            Alertes de diagnostic
          </h3>
          <div className="space-y-3">
            {report.warnings.map((warning) => (
              <div
                className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-950"
                key={warning.code}
                role="status"
              >
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
                <p className="text-sm leading-6">{warning.message}</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}

function RuntimeMetric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ListChecks;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardBody>
        <Icon aria-hidden="true" className="size-5 text-forest-600" />
        <p className="mt-5 text-2xl font-bold tracking-[-0.02em] text-slate-900">{value}</p>
        <p className="mt-1 text-sm font-semibold text-slate-700">{label}</p>
      </CardBody>
    </Card>
  );
}

function QueueMetric({
  label,
  value,
  icon = false,
}: {
  label: string;
  value: string;
  icon?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-4">
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {icon && <Clock3 aria-hidden="true" className="size-3.5" />}
        {label}
      </p>
      <p className="mt-2 text-xl font-bold text-slate-900">{value}</p>
    </div>
  );
}
