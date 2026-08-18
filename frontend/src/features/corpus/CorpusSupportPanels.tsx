import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import {
  attentionIngestionJobs,
  hasMoreIngestionJobs,
  ingestionJournalPageSizes,
  type IngestionJournalPageSize,
  visibleIngestionJobs,
} from "@/features/corpus/ingestionJournal";
import { formatDate } from "@/lib/cn";
import { statusTone } from "@/lib/status";
import type { CorpusArticle, IngestionJob } from "@/types/api";

export function CorpusActivityPanel({
  jobs,
  attentionOnly = false,
  retryFailed,
  setRetryFailed,
}: {
  jobs: IngestionJob[];
  attentionOnly?: boolean;
  retryFailed: boolean;
  setRetryFailed: (retry: boolean) => void;
}) {
  const [pageSize, setPageSize] = useState<IngestionJournalPageSize>(25);
  const [visibleCount, setVisibleCount] = useState(25);
  const filteredJobs = attentionOnly ? attentionIngestionJobs(jobs) : jobs;
  const visibleJobs = visibleIngestionJobs(filteredJobs, visibleCount);
  const hasMore = hasMoreIngestionJobs(filteredJobs, visibleCount);

  return (
    <Card>
      <CardHeader className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-bold text-slate-900">Journal d’ingestion</h2>
          <p className="mt-1 text-xs text-slate-500">
            {attentionOnly
              ? "Opérations nécessitant une intervention"
              : `${jobs.length} opération${jobs.length > 1 ? "s" : ""} locale${jobs.length > 1 ? "s" : ""}`}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs font-semibold text-slate-600">
          <input
            checked={retryFailed}
            className="size-4 accent-forest-700"
            onChange={(event) => setRetryFailed(event.target.checked)}
            type="checkbox"
          />
          Réessayer les échecs à la prochaine indexation
        </label>
      </CardHeader>
      <div className="divide-y divide-slate-100">
        {visibleJobs.map((job) => (
          <div
            className="grid gap-2 px-5 py-4 md:grid-cols-[1fr_auto_auto] md:items-center"
            key={job.id}
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-800">{job.pdf_path}</p>
              <p className="mt-1 text-xs text-slate-500">
                {job.error_message ?? `Tentative ${job.attempt_count}`}
              </p>
            </div>
            <Badge tone={statusTone(job.state)}>{job.state}</Badge>
            <p className="text-xs text-slate-400">{formatDate(job.updated_at)}</p>
          </div>
        ))}
        {filteredJobs.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-slate-500" role="status">
            {attentionOnly
              ? "Aucune opération ne nécessite d’intervention."
              : "Aucune opération d’ingestion à afficher."}
          </p>
        )}
      </div>
      {filteredJobs.length > 0 && (
        <div className="flex flex-col-reverse gap-3 border-t border-slate-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            Afficher
            <select
              aria-label="Nombre d’opérations affichées à la fois"
              className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
              onChange={(event) => {
                const nextPageSize = Number(event.target.value) as IngestionJournalPageSize;
                setPageSize(nextPageSize);
                setVisibleCount(nextPageSize);
              }}
              value={pageSize}
            >
              {ingestionJournalPageSizes.map((size) => (
                <option key={size} value={size}>
                  {size} par page
                </option>
              ))}
            </select>
            sorties
          </label>
          {hasMore && (
            <Button
              onClick={() => setVisibleCount((previous) => previous + pageSize)}
              variant="secondary"
            >
              Voir plus
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

export function DeleteArticleDialog({
  target,
  busy,
  onClose,
  onConfirm,
}: {
  target: CorpusArticle | null;
  busy: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} variant="secondary">
            Annuler
          </Button>
          <Button loading={busy === "delete"} onClick={onConfirm} variant="danger">
            Supprimer
          </Button>
        </div>
      }
      onClose={onClose}
      open={target !== null}
      title="Supprimer cet article ?"
    >
      <p className="text-sm leading-6 text-slate-600">
        Les métadonnées, fragments, vecteurs et analyses dépendantes de
        <strong className="text-slate-900"> {target?.title}</strong> seront supprimés. Le fichier
        PDF d’origine restera intact.
      </p>
    </Dialog>
  );
}
