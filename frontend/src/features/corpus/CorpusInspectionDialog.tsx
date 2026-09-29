import { CorpusDetailDialog } from "@/features/corpus/CorpusDetailDialog";
import type { CorpusArticle, CorpusInspection } from "@/types/api";

export function CorpusInspectionDialog({
  article,
  data,
  error,
  loading,
  onClose,
  retry,
}: {
  article: CorpusArticle | null;
  data: CorpusInspection | null;
  error: string | null;
  loading: boolean;
  onClose: () => void;
  retry: () => void;
}) {
  return (
    <CorpusDetailDialog
      article={article}
      error={error}
      loading={loading}
      loadingLabel="Lecture de la provenance…"
      onClose={onClose}
      retry={retry}
      title="Inspection"
    >
      {data && (
        <div className="space-y-5 text-sm text-slate-700">
          <p className="text-xs text-slate-500">
            {data.totals.assets} source(s) · {data.totals.outline} nœud(s) · {data.totals.chunks}{" "}
            fragment(s). Le texte source n’est pas affiché ici.
          </p>
          <section>
            <h3 className="font-bold text-slate-900">Structure</h3>
            {data.outline.length ? (
              <ol className="mt-2 space-y-1">
                {data.outline.map((node) => (
                  <li
                    className="rounded bg-slate-50 px-3 py-2"
                    key={`${node.source_locator}-${node.title}`}
                    style={{ marginLeft: `${Math.max(0, node.level - 1) * 12}px` }}
                  >
                    {node.title}{" "}
                    <span className="text-xs text-slate-500">{node.source_locator}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-2 text-slate-500">Aucun outline extrait.</p>
            )}
          </section>
          <section>
            <h3 className="font-bold text-slate-900">Extraction</h3>
            {data.extraction_runs.length ? (
              <ul className="mt-2 space-y-1">
                {data.extraction_runs.map((run) => (
                  <li
                    className="rounded bg-slate-50 px-3 py-2"
                    key={`${run.parser_id}-${run.parser_version}`}
                  >
                    {run.parser_id} {run.parser_version} · {run.state} · {run.warning_count}{" "}
                    avertissement(s)
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-slate-500">Aucune exécution rattachée.</p>
            )}
          </section>
        </div>
      )}
    </CorpusDetailDialog>
  );
}
