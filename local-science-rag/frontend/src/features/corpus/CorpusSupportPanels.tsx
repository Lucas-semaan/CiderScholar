import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { formatDate } from "@/lib/cn";
import { statusTone } from "@/lib/status";
import type { CorpusArticle, IngestionJob } from "@/types/api";

export function CorpusActivityPanel({
  jobs,
  retryFailed,
  setRetryFailed,
}: {
  jobs: IngestionJob[];
  retryFailed: boolean;
  setRetryFailed: (retry: boolean) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-bold text-slate-900">Journal d’ingestion</h2>
          <p className="mt-1 text-xs text-slate-500">200 dernières opérations locales</p>
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
        {jobs.map((job) => (
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
      </div>
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
