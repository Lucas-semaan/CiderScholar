import { CorpusDetailDialog } from "@/features/corpus/CorpusDetailDialog";
import type { CorpusArticle, NativeSourceView } from "@/types/api";

export function NativeSourceDialog({
  article,
  data,
  error,
  loading,
  onClose,
  retry,
}: {
  article: CorpusArticle | null;
  data: NativeSourceView | null;
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
      loadingLabel="Lecture de la source structurée…"
      onClose={onClose}
      retry={retry}
      title="Source native"
    >
      {data && (
        <div className="space-y-5 text-sm text-slate-700">
          <p className="text-xs text-slate-500">
            {data.assets.length} source(s) vérifiée(s) · {data.passages.length} passage(s). Le XML
            est affiché comme données structurées, jamais comme HTML.
          </p>
          {data.passages.length ? (
            <ol className="space-y-3">
              {data.passages.map((passage) => (
                <li
                  className="rounded-xl border border-slate-200 bg-slate-50 p-4"
                  key={passage.chunk_id}
                >
                  <p className="text-xs font-bold text-forest-700">
                    § {passage.section_path}, par. {passage.paragraph_start}
                    {passage.paragraph_end > passage.paragraph_start
                      ? `–${passage.paragraph_end}`
                      : ""}
                    {passage.xml_id_start ? ` · ${passage.xml_id_start}` : ""}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap leading-6 text-slate-800">
                    {passage.text}
                  </p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="rounded-xl bg-slate-50 p-4 text-slate-500">
              Aucune source JATS ou TEI admise pour cet article.
            </p>
          )}
        </div>
      )}
    </CorpusDetailDialog>
  );
}
