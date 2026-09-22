import { Dialog } from "@/components/ui/Dialog";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import type { CorpusArticle, FigureEvidenceView } from "@/types/api";

export function FigureEvidenceDialog({
  article,
  data,
  error,
  loading,
  onClose,
  retry,
}: {
  article: CorpusArticle | null;
  data: FigureEvidenceView | null;
  error: string | null;
  loading: boolean;
  onClose: () => void;
  retry: () => void;
}) {
  return (
    <Dialog
      onClose={onClose}
      open={article !== null}
      title={article ? `Figures source — ${article.title}` : "Figures source"}
    >
      {loading && <LoadingState label="Lecture des légendes source…" />}
      {error && <ErrorState message={error} retry={retry} />}
      {data && (
        <div className="space-y-5 text-sm text-slate-700">
          <p className="text-xs text-slate-500">
            {data.figures.length} figure(s) source. Seules les légendes originales et leurs liens
            persistés sont affichés ; aucune analyse visuelle générée n’est présentée comme preuve.
          </p>
          {data.figures.length ? (
            <ol className="space-y-3">
              {data.figures.map((figure, index) => (
                <li
                  className="rounded-xl border border-slate-200 bg-slate-50 p-4"
                  key={figure.element_id}
                >
                  <p className="text-xs font-bold text-forest-700">
                    Figure {index + 1} · page {figure.page_number}
                    {figure.related_chunk_ids.length
                      ? ` · ${figure.related_chunk_ids.length} fragment(s) lié(s)`
                      : ""}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap leading-6 text-slate-800">
                    {figure.original_caption ?? "Légende source absente."}
                  </p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="rounded-xl bg-slate-50 p-4 text-slate-500">
              Aucune figure source persistante pour cet article.
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
