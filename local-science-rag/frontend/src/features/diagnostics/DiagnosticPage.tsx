import { useCallback } from "react";

import { AlertTriangle, CheckCircle2, Clock3, ListChecks, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";

import { diagnosticLabels, formatQueueAge } from "./diagnosticPresentation";

export function DiagnosticPage() {
  const loadReadiness = useCallback(() => api.diagnostics.readiness(), []);
  const { data, error, loading, refresh } = useRemoteData(loadReadiness);

  if (loading && !data) return <LoadingState label="Contrôle du poste…" />;
  if (error && !data) return <ErrorState message={error} retry={refresh} />;
  if (!data) return null;

  return (
    <div className="space-y-8">
      <PageHeader
        actions={
          <Button loading={loading} onClick={() => void refresh()} variant="secondary">
            <RefreshCw aria-hidden="true" className="size-4" />
            Actualiser les contrôles
          </Button>
        }
        description="Vérifie les services nécessaires sans générer de texte scientifique ni exposer le contenu de la file."
        eyebrow="Préparation"
        title="Diagnostic de démonstration"
      />

      {error && <ErrorState message={error} retry={refresh} />}

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
