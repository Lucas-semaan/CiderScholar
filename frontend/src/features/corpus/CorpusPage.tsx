import { useCallback, useState } from "react";

import { AlertTriangle, CheckCircle2, FileText, Layers3, UploadCloud } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { MetricCard } from "@/components/ui/MetricCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { JobStatusCard } from "@/features/chatbot/JobStatusCard";
import { pollDurableJob } from "@/features/chatbot/jobPolling";
import { CorpusArticlesPanel } from "@/features/corpus/CorpusArticlesPanel";
import { CorpusImportPanel } from "@/features/corpus/CorpusImportPanel";
import { CorpusActivityPanel, DeleteArticleDialog } from "@/features/corpus/CorpusSupportPanels";
import { ingestionOutcomeMessage } from "@/features/corpus/ingestionSummary";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/cn";
import { corpusDestinations, corpusTabFromQuery, type CorpusTab } from "@/lib/navigation";
import type { CorpusArticle, DurableJob, IngestionReport } from "@/types/api";

const corpusTabs: Array<{ id: CorpusTab; label: string }> = [
  { id: "articles", label: "Articles" },
  { id: "import", label: "Importer" },
  { id: "activity", label: "Journal d’ingestion" },
];

export function CorpusPage({ embedded = false }: { embedded?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data, error, loading, refresh } = useRemoteData(useCallback(() => api.corpus.list(), []));
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [retryFailed, setRetryFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reports, setReports] = useState<IngestionReport[]>([]);
  const [queuedJob, setQueuedJob] = useState<DurableJob | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<CorpusArticle | null>(null);
  const tab = corpusTabFromQuery(searchParams.get("tab"));
  const selectTab = (nextTab: CorpusTab) =>
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("tab", nextTab);
      return next;
    });
  const runAction = useCallback(
    async <Result,>(
      name: string,
      action: () => Promise<Result>,
      success: string | ((result: Result) => string),
    ) => {
      setBusy(name);
      setNotice(null);
      setActionError(null);
      try {
        const result = await action();
        setNotice(typeof success === "function" ? success(result) : success);
        refresh();
      } catch (caught: unknown) {
        setActionError(caught instanceof Error ? caught.message : "Erreur inconnue");
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );
  const trackQueuedJob = useCallback(
    (initialJob: DurableJob) => {
      setQueuedJob(initialJob);
      void pollDurableJob(initialJob, { poll: api.jobs.poll, onUpdate: setQueuedJob })
        .then((finalJob) => {
          setQueuedJob(finalJob);
          refresh();
          if (finalJob.state === "succeeded") setNotice("Ingestion terminée.");
        })
        .catch((caught: unknown) =>
          setActionError(
            caught instanceof Error ? caught.message : "Suivi du travail indisponible.",
          ),
        );
    },
    [refresh],
  );
  const upload = () =>
    void runAction(
      "upload",
      async () => {
        const response = await api.corpus.upload(files);
        const completedReports = response.reports ?? [];
        setReports(completedReports);
        if (response.job) trackQueuedJob(response.job);
        setFiles([]);
        return {
          reports: completedReports,
          expectedFileCount: response.staged_files ?? files.length,
        };
      },
      ({ reports: completedReports, expectedFileCount }) =>
        ingestionOutcomeMessage(completedReports, expectedFileCount),
    );
  const ingestFolder = () =>
    void runAction(
      "folder",
      async () => {
        const response = await api.corpus.folder(folder, recursive);
        const completedReports = response.reports ?? [];
        setReports(completedReports);
        if (response.job) trackQueuedJob(response.job);
        return {
          reports: completedReports,
          expectedFileCount: response.discovered_files,
        };
      },
      ({ reports: completedReports, expectedFileCount }) =>
        ingestionOutcomeMessage(completedReports, expectedFileCount),
    );

  if (loading && !data) return <LoadingState label="Lecture du corpus PDF…" />;
  if (error && !data) return <ErrorState message={error} retry={refresh} />;
  if (!data) return null;
  const destinations = corpusDestinations();
  return (
    <div className="space-y-8">
      <PageHeader
        actions={
          <Button onClick={() => selectTab("import")}>
            <UploadCloud aria-hidden="true" className="size-4" />
            Ajouter des documents
          </Button>
        }
        description="Ajoutez des PDF à la base documentaire et contrôlez l’extraction de leur texte ainsi que leur indexation locale."
        eyebrow="Base documentaire"
        title={embedded ? "Administration documentaire" : "Base documentaire"}
      />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          actionLabel="Afficher les articles PDF locaux"
          icon={FileText}
          label="Articles"
          note="Documents scientifiques locaux"
          to={destinations.articles}
          value={formatNumber(data.summary.articles)}
        />
        <MetricCard
          actionLabel="Interroger l’assistant scientifique sur ces documents"
          icon={Layers3}
          label="Fragments"
          note="Unités de preuve page par page"
          tone="sky"
          to={destinations.assistant}
          value={formatNumber(data.summary.chunks)}
        />
        <MetricCard
          actionLabel="Interroger l’assistant scientifique sur les fragments indexés"
          icon={CheckCircle2}
          label="Indexés"
          note="Disponibles pour la recherche hybride"
          tone="cider"
          to={destinations.assistant}
          value={formatNumber(data.summary.indexed_chunks)}
        />
        <MetricCard
          actionLabel="Ouvrir les opérations à surveiller"
          icon={AlertTriangle}
          label="À surveiller"
          note={`${data.summary.ocr_jobs} OCR · ${data.summary.failed_jobs} échec(s)`}
          tone="slate"
          to={destinations.activity}
          value={formatNumber(data.summary.ocr_jobs + data.summary.failed_jobs)}
        />
      </section>
      <nav
        aria-label="Sections du corpus"
        className="flex gap-1 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm"
      >
        {corpusTabs.map((item) => (
          <button
            aria-current={tab === item.id ? "page" : undefined}
            className={
              tab === item.id
                ? "min-h-11 whitespace-nowrap rounded-xl bg-forest-600 px-4 text-sm font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
                : "min-h-11 whitespace-nowrap rounded-xl px-4 text-sm font-semibold text-slate-600 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
            }
            key={item.id}
            onClick={() => selectTab(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>
      {(notice || actionError) && (
        <div
          className={
            actionError
              ? "rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
              : "rounded-xl border border-forest-200 bg-forest-50 px-4 py-3 text-sm text-forest-700"
          }
          role={actionError ? "alert" : "status"}
        >
          {actionError ?? notice}
        </div>
      )}
      {queuedJob && (
        <JobStatusCard
          job={queuedJob}
          onCancel={async (job) => setQueuedJob(await api.jobs.cancel(job.id))}
          onRetry={async (job) => trackQueuedJob(await api.jobs.retry(job.id, crypto.randomUUID()))}
        />
      )}
      {tab === "articles" && (
        <CorpusArticlesPanel
          articles={data.articles}
          busy={busy}
          onDelete={setDeleteTarget}
          onIndex={() =>
            void runAction(
              "index",
              () => api.corpus.index(retryFailed),
              "Index vectoriel actualisé.",
            )
          }
          onReindex={(article) =>
            void runAction(
              `reindex-${article.id}`,
              () => api.corpus.reindex(article.id),
              "Article réindexé.",
            )
          }
        />
      )}
      {tab === "import" && (
        <CorpusImportPanel
          busy={busy}
          files={files}
          folder={folder}
          onFilesChange={setFiles}
          onFolderChange={setFolder}
          onFolderIngest={ingestFolder}
          onRecursiveChange={setRecursive}
          onUpload={upload}
          recursive={recursive}
          reports={reports}
        />
      )}
      {tab === "activity" && (
        <CorpusActivityPanel
          jobs={data.jobs}
          retryFailed={retryFailed}
          setRetryFailed={setRetryFailed}
        />
      )}
      <DeleteArticleDialog
        busy={busy}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return;
          void runAction(
            "delete",
            () => api.corpus.remove(deleteTarget.id),
            "Métadonnées et vecteurs supprimés ; le PDF source est conservé.",
          ).then(() => setDeleteTarget(null));
        }}
        target={deleteTarget}
      />
    </div>
  );
}
