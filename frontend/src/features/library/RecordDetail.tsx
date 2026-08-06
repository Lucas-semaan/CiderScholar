import { useId, useState } from "react";

import { CheckCircle2, ExternalLink, FileText, ShieldX } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/cn";
import { statusTone } from "@/lib/status";
import type { LibraryRecord } from "@/types/api";

const statusLabels: Record<string, string> = {
  accepted: "Acceptée",
  review: "À réviser",
  unreviewed: "Non évaluée",
  rejected: "Rejetée",
};

function authors(value: string): string[] {
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

export function RecordDetail({
  record,
  onReviewed,
}: {
  record: LibraryRecord;
  onReviewed: (message: string, recordId: string) => void;
}) {
  return (
    <Card className="h-fit xl:sticky xl:top-24">
      <CardHeader>
        <RecordDetailHeader record={record} />
      </CardHeader>
      <CardBody>
        <RecordDetailBody onReviewed={onReviewed} record={record} />
      </CardBody>
    </Card>
  );
}

export function RecordDetailHeader({ record }: { record: LibraryRecord }) {
  return (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Badge tone={record.document_type === "full_text" ? "info" : "neutral"}>
            {record.document_type === "full_text" ? "Full article" : "Abstract only"}
          </Badge>
          {record.relevance_status !== "accepted" && (
            <Badge tone={statusTone(record.relevance_status)}>
              {statusLabels[record.relevance_status]}
            </Badge>
          )}
        </div>
        {record.doi && (
          <a
            aria-label={`Ouvrir le DOI de ${record.title}`}
            className="grid size-9 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-forest-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
            href={`https://doi.org/${record.doi}`}
            rel="noreferrer"
            target="_blank"
          >
            <ExternalLink aria-hidden="true" className="size-4" />
          </a>
        )}
      </div>
      <h2 className="mt-4 text-lg font-bold leading-7 text-slate-900">{record.title}</h2>
    </>
  );
}

export function RecordDetailBody({
  record,
  onReviewed,
}: {
  record: LibraryRecord;
  onReviewed: (message: string, recordId: string) => void;
}) {
  const recordAuthors = authors(record.authors);
  return (
    <div className="space-y-5">
      {record.relevance_status === "review" && (
        <RecordReviewDecision key={record.id} onReviewed={onReviewed} record={record} />
      )}
      {record.pdf_available && record.article_id && (
        <a
          className="inline-flex min-h-[42px] w-full items-center justify-center gap-2 rounded-[10px] border border-forest-600 bg-forest-600 px-[18px] py-2.5 text-sm font-semibold leading-none text-white transition hover:border-forest-700 hover:bg-forest-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-600 focus-visible:ring-offset-2"
          href={`/api/corpus/${encodeURIComponent(record.article_id)}/pdf`}
          rel="noreferrer"
          target="_blank"
        >
          <FileText aria-hidden="true" className="size-4" />
          Ouvrir le PDF
        </a>
      )}
      <dl className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-xs text-slate-400">Année</dt>
          <dd className="mt-1 font-semibold">{record.publication_year ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Citations</dt>
          <dd className="mt-1 font-semibold">{record.citation_count ?? "—"}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-slate-400">Journal</dt>
          <dd className="mt-1 font-semibold">{record.journal ?? "—"}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-slate-400">Auteurs</dt>
          <dd className="mt-1 leading-6 text-slate-700">{recordAuthors.join(", ") || "—"}</dd>
        </div>
        {record.document_type === "full_text" && (
          <div className="col-span-2">
            <dt className="text-xs text-slate-400">Indexation du texte</dt>
            <dd className="mt-1 font-semibold">
              {record.indexed_chunk_count}/{record.chunk_count} fragments indexés
            </dd>
          </div>
        )}
      </dl>
      {record.doi && (
        <div className="rounded-xl bg-forest-50 p-3">
          <p className="text-[10px] font-bold uppercase tracking-wider text-forest-600">
            DOI vérifié
          </p>
          <p className="mt-1 break-all font-mono text-xs text-forest-800">{record.doi}</p>
        </div>
      )}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Abstract</h3>
        <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-slate-600">
          {record.abstract ?? "Abstract indisponible."}
        </p>
      </div>
      {record.relevance_reason && (
        <div>
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
            Qualification
          </h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">{record.relevance_reason}</p>
        </div>
      )}
      <div className="border-t border-slate-100 pt-4 text-xs leading-5 text-slate-400">
        <p>Sources : {record.sources ?? "—"}</p>
        <p>Dernière observation : {formatDate(record.last_seen_at)}</p>
      </div>
    </div>
  );
}

function RecordReviewDecision({
  record,
  onReviewed,
}: {
  record: LibraryRecord;
  onReviewed: (message: string, recordId: string) => void;
}) {
  const legendId = useId();
  const [decision, setDecision] = useState<"" | "accepted" | "rejected">("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyDecision = async () => {
    if (!decision) return;
    setPending(true);
    setError(null);
    try {
      const result = await api.library.decideReview(record.id, decision);
      onReviewed(
        result.decision === "accepted"
          ? "Le document a été validé dans la base scientifique et sera indexé."
          : "L’article a été rejeté et supprimé intégralement de la base.",
        record.id,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "La décision n’a pas pu être appliquée.");
    } finally {
      setPending(false);
    }
  };

  return (
    <section
      aria-labelledby={legendId}
      className="rounded-2xl border border-amber-200 bg-amber-50 p-4"
    >
      <fieldset>
        <legend className="text-sm font-bold text-amber-950" id={legendId}>
          Décision de révision
        </legend>
        <p className="mt-1 text-xs leading-5 text-amber-800">
          Choisissez si cette référence doit être retenue pour les recherches scientifiques.
        </p>
        <div className="mt-3 grid gap-2">
          <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-slate-800 has-[:checked]:border-forest-600 has-[:checked]:ring-2 has-[:checked]:ring-forest-600/20">
            <input
              checked={decision === "accepted"}
              className="size-4 accent-forest-600"
              disabled={pending}
              name={`review-${record.id}`}
              onChange={() => setDecision("accepted")}
              type="radio"
            />
            <CheckCircle2 aria-hidden="true" className="size-4 text-forest-600" />
            Retenir dans la base
          </label>
          <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-slate-800 has-[:checked]:border-red-600 has-[:checked]:ring-2 has-[:checked]:ring-red-600/20">
            <input
              checked={decision === "rejected"}
              className="size-4 accent-red-700"
              disabled={pending}
              name={`review-${record.id}`}
              onChange={() => setDecision("rejected")}
              type="radio"
            />
            <ShieldX aria-hidden="true" className="size-4 text-red-700" />
            Rejeter et supprimer
          </label>
        </div>
        {error && (
          <p className="mt-3 text-xs font-semibold leading-5 text-red-700" role="alert">
            {error}
          </p>
        )}
        <Button
          className="mt-3 w-full"
          disabled={!decision}
          loading={pending}
          onClick={() => void applyDecision()}
          variant={decision === "rejected" ? "danger" : "primary"}
        >
          Appliquer la décision
        </Button>
      </fieldset>
    </section>
  );
}
