import { Dialog } from "@/components/ui/Dialog";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import type { CorpusArticle, TableEvidenceView } from "@/types/api";

export function TableEvidenceDialog({
  article,
  data,
  error,
  loading,
  onClose,
  retry,
}: {
  article: CorpusArticle | null;
  data: TableEvidenceView | null;
  error: string | null;
  loading: boolean;
  onClose: () => void;
  retry: () => void;
}) {
  return (
    <Dialog
      onClose={onClose}
      open={article !== null}
      title={article ? `Tableaux source — ${article.title}` : "Tableaux source"}
    >
      {loading && <LoadingState label="Lecture des cellules source…" />}
      {error && <ErrorState message={error} retry={retry} />}
      {data && (
        <div className="space-y-5 text-sm text-slate-700">
          <p className="text-xs text-slate-500">
            {data.tables.length} tableau(x) extrait(s) de la source. Les cellules ci-dessous sont
            persistées ; aucune valeur ou légende générée n’est affichée.
          </p>
          {data.tables.length ? (
            data.tables.map((table, index) => (
              <section className="space-y-2" key={table.element_id}>
                <p className="text-xs font-bold text-forest-700">
                  Tableau {index + 1} · page {table.page_number}
                  {table.related_chunk_ids.length
                    ? ` · ${table.related_chunk_ids.length} fragment(s) lié(s)`
                    : ""}
                </p>
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                  <table className="min-w-full border-collapse text-left text-xs">
                    <tbody>
                      {rows(table.cells).map(([rowIndex, cells]) => (
                        <tr className="border-b border-slate-100 last:border-0" key={rowIndex}>
                          {cells.map((cell) => (
                            <td
                              className="border-r border-slate-100 p-3 last:border-0"
                              key={cell.column_index}
                            >
                              {cell.text}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))
          ) : (
            <p className="rounded-xl bg-slate-50 p-4 text-slate-500">
              Aucun tableau source persistant pour cet article.
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}

function rows(cells: TableEvidenceView["tables"][number]["cells"]) {
  const grouped = new Map<number, typeof cells>();
  for (const cell of cells) {
    grouped.set(cell.row_index, [...(grouped.get(cell.row_index) ?? []), cell]);
  }
  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(
      ([index, row]) =>
        [index, [...row].sort((left, right) => left.column_index - right.column_index)] as const,
    );
}
