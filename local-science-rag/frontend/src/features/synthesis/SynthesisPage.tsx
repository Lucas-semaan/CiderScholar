import { useCallback, useMemo, useState } from "react";

import {
  BookCheck,
  CheckCircle2,
  Download,
  FileQuestion,
  FlaskConical,
  Play,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/Feedback";
import { Select } from "@/components/ui/Form";
import { PageHeader } from "@/components/ui/PageHeader";
import { JobStatusCard } from "@/features/chatbot/JobStatusCard";
import { pollDurableJob } from "@/features/chatbot/jobPolling";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/cn";
import { statusTone } from "@/lib/status";
import type { DurableJob, SynthesisDetail } from "@/types/api";

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function SynthesisPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [queuedJob, setQueuedJob] = useState<DurableJob | null>(null);
  const loadQueries = useCallback(() => api.synthesis.list(), []);
  const queries = useRemoteData(loadQueries);
  const activeId = selectedId ?? queries.data?.queries[0]?.id ?? null;
  const loadDetail = useCallback(
    () => (activeId ? api.synthesis.detail(activeId) : Promise.resolve(null)),
    [activeId],
  );
  const detail = useRemoteData<SynthesisDetail | null>(loadDetail);
  const activeQuery = useMemo(
    () => queries.data?.queries.find((query) => query.id === activeId) ?? null,
    [activeId, queries.data],
  );

  const trackJob = (initialJob: DurableJob) => {
    setQueuedJob(initialJob);
    void pollDurableJob(initialJob, {
      poll: api.jobs.poll,
      onUpdate: setQueuedJob,
    })
      .then((finalJob) => {
        setQueuedJob(finalJob);
        detail.refresh();
        queries.refresh();
      })
      .catch((caught: unknown) => {
        setActionError(caught instanceof Error ? caught.message : "Suivi du travail indisponible.");
      });
  };

  if (queries.loading && !queries.data) return <LoadingState label="Lecture des analyses…" />;
  if (queries.error && !queries.data)
    return <ErrorState message={queries.error} retry={queries.refresh} />;

  const runSynthesis = async () => {
    if (!activeId) return;
    setBusy(true);
    setActionError(null);
    try {
      const initialJob = await api.synthesis.run(activeId, true);
      trackJob(initialJob);
    } catch (caught: unknown) {
      setActionError(caught instanceof Error ? caught.message : "Erreur inconnue");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader
        description="Reprenez les analyses persistées, contrôlez leurs preuves et produisez une synthèse hiérarchique traçable."
        eyebrow="Production scientifique"
        title="Synthèses"
        actions={
          activeId ? (
            <Button loading={busy} onClick={() => void runSynthesis()}>
              {detail.data?.result ? (
                <RefreshCw aria-hidden="true" className="size-4" />
              ) : (
                <Play aria-hidden="true" className="size-4" />
              )}
              {detail.data?.result ? "Reprendre la synthèse" : "Lancer la synthèse"}
            </Button>
          ) : null
        }
      />

      {!queries.data?.queries.length ? (
        <EmptyState
          description="Classez des articles puis extrayez leurs preuves depuis la page Recherche."
          icon={FileQuestion}
          title="Aucune analyse enregistrée"
        />
      ) : (
        <>
          <Card>
            <CardBody className="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
              <label className="grid gap-1.5 text-sm font-medium text-slate-700">
                Analyse enregistrée
                <Select
                  onChange={(event) => setSelectedId(event.target.value)}
                  value={activeId ?? ""}
                >
                  {queries.data.queries.map((query) => (
                    <option key={query.id} value={query.id}>
                      {query.original_query} — {query.id.slice(0, 8)}
                    </option>
                  ))}
                </Select>
              </label>
              <Badge tone={statusTone(activeQuery?.synthesis_state ?? "pending")}>
                {activeQuery?.synthesis_state ?? "non lancée"}
              </Badge>
            </CardBody>
          </Card>

          <div aria-live="polite">
            {actionError && (
              <div
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
                role="alert"
              >
                {actionError}
              </div>
            )}
          </div>
          {queuedJob && (
            <JobStatusCard
              job={queuedJob}
              onCancel={async (job) => {
                setQueuedJob(await api.jobs.cancel(job.id));
              }}
              onRetry={async (job) => {
                const retried = await api.jobs.retry(job.id, crypto.randomUUID());
                trackJob(retried);
              }}
            />
          )}
          {detail.error && <ErrorState message={detail.error} retry={detail.refresh} />}
          {detail.loading && !detail.data ? (
            <LoadingState label="Reconstruction de la synthèse depuis SQLite…" />
          ) : detail.data ? (
            <SynthesisWorkspace detail={detail.data} />
          ) : null}
        </>
      )}
    </div>
  );
}

function SynthesisWorkspace({ detail }: { detail: SynthesisDetail }) {
  const result = detail.result;
  return (
    <div className="space-y-6">
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatusMetric
          icon={BookCheck}
          label="Articles sélectionnés"
          value={detail.summary.selected_article_ids.length}
        />
        <StatusMetric
          icon={CheckCircle2}
          label="Fiches terminées"
          value={detail.summary.evidence_completed}
        />
        <StatusMetric
          icon={TriangleAlert}
          label="Fiches en échec"
          value={detail.summary.evidence_failed}
        />
        <StatusMetric icon={FlaskConical} label="Thèmes" value={detail.themes.length} />
      </section>

      <div className="grid gap-6 xl:grid-cols-[.72fr_1.28fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <h2 className="font-bold text-slate-900">Fiches de preuve</h2>
              <p className="mt-1 text-xs text-slate-500">
                Créées {formatDate(detail.summary.created_at)}
              </p>
            </CardHeader>
            <div className="divide-y divide-slate-100">
              {detail.evidence_runs.map((run, index) => (
                <EvidenceRun key={String(run.article_id ?? index)} run={run} />
              ))}
            </div>
          </Card>

          {detail.themes.length > 0 && (
            <Card>
              <CardHeader>
                <h2 className="font-bold text-slate-900">Thèmes intermédiaires</h2>
              </CardHeader>
              <div className="divide-y divide-slate-100">
                {detail.themes.map((theme, index) => (
                  <ThemeSummary key={index} theme={theme} />
                ))}
              </div>
            </Card>
          )}
        </div>

        <Card className="h-fit xl:sticky xl:top-24">
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Synthèse finale</h2>
              <p className="mt-1 text-xs text-slate-500">
                Rendue depuis les preuves SQLite autorisées
              </p>
            </div>
            {result && (
              <div className="flex gap-2">
                <Button
                  onClick={() =>
                    download(
                      `synthese-${detail.summary.id}.md`,
                      result.answer_markdown,
                      "text/markdown",
                    )
                  }
                  variant="secondary"
                >
                  <Download aria-hidden="true" className="size-4" /> Markdown
                </Button>
                <Button
                  onClick={() =>
                    download(
                      `synthese-${detail.summary.id}.json`,
                      JSON.stringify(result, null, 2),
                      "application/json",
                    )
                  }
                  variant="secondary"
                >
                  JSON
                </Button>
              </div>
            )}
          </CardHeader>
          <CardBody>
            {result ? (
              <div className="space-y-4 text-sm leading-7 text-slate-700 [&_a]:font-semibold [&_a]:text-forest-700 [&_h1]:mt-7 [&_h1]:text-2xl [&_h1]:font-bold [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-bold [&_li]:ml-5 [&_li]:list-disc [&_p]:my-3 [&_strong]:text-slate-900">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer_markdown}</ReactMarkdown>
              </div>
            ) : (
              <div className="grid min-h-72 place-items-center text-center">
                <div className="max-w-sm">
                  <FlaskConical className="mx-auto size-10 text-slate-300" />
                  <h3 className="mt-4 font-bold text-slate-800">Synthèse non générée</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Lancez le traitement pour regrouper les fiches par thème et produire la réponse
                    finale.
                  </p>
                </div>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function StatusMetric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof BookCheck;
  label: string;
  value: number;
}) {
  return (
    <Card>
      <CardBody className="flex items-center gap-4">
        <span className="grid size-10 place-items-center rounded-xl bg-forest-50 text-forest-700">
          <Icon aria-hidden="true" className="size-5" />
        </span>
        <div>
          <p className="text-2xl font-bold text-slate-900">{value}</p>
          <p className="text-xs font-semibold text-slate-500">{label}</p>
        </div>
      </CardBody>
    </Card>
  );
}

function EvidenceRun({ run }: { run: Record<string, unknown> }) {
  const title = typeof run.title === "string" ? run.title : "Article";
  const state = typeof run.state === "string" ? run.state : "pending";
  const evidence =
    typeof run.evidence === "object" && run.evidence !== null
      ? (run.evidence as Record<string, unknown>)
      : null;
  const findings = Array.isArray(evidence?.findings) ? evidence.findings : [];
  return (
    <details className="group px-5 py-4">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-600 focus-visible:ring-offset-2">
        <div>
          <p className="text-sm font-semibold leading-6 text-slate-800">{title}</p>
          <p className="mt-1 text-xs text-slate-400">{findings.length} constat(s)</p>
        </div>
        <Badge tone={statusTone(state)}>{state}</Badge>
      </summary>
      {findings.length > 0 && (
        <ul className="mt-4 space-y-3 border-l-2 border-forest-100 pl-4">
          {findings.map((finding, index) => {
            const item =
              typeof finding === "object" && finding !== null
                ? (finding as Record<string, unknown>)
                : {};
            return (
              <li className="text-sm leading-6 text-slate-600" key={index}>
                {String(item.claim ?? "Constat scientifique")}
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}

function ThemeSummary({ theme }: { theme: Record<string, unknown> }) {
  const assignment =
    typeof theme.assignment === "object" && theme.assignment !== null
      ? (theme.assignment as Record<string, unknown>)
      : {};
  const state = typeof theme.state === "string" ? theme.state : "pending";
  return (
    <div className="flex items-start justify-between gap-3 px-5 py-4">
      <div>
        <p className="text-sm font-semibold text-slate-800">
          {String(assignment.label ?? assignment.theme_id ?? "Thème")}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          {Array.isArray(assignment.article_ids) ? assignment.article_ids.length : 0} article(s)
        </p>
      </div>
      <Badge tone={statusTone(state)}>{state}</Badge>
    </div>
  );
}
