import { useEffect, useMemo, useState } from "react";

import { BookOpenText, Eye, Image, RefreshCw, Table2, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { statusTone } from "@/lib/status";
import type { CorpusArticle } from "@/types/api";

interface CorpusArticlesPanelProps {
  articles: CorpusArticle[];
  busy: string | null;
  onIndex: () => void;
  onReindex: (article: CorpusArticle) => void;
  onDelete: (article: CorpusArticle) => void;
  onInspect: (article: CorpusArticle) => void;
  onViewNativeSource: (article: CorpusArticle) => void;
  onViewTables: (article: CorpusArticle) => void;
  onViewFigures: (article: CorpusArticle) => void;
}

const articlesPerPage = 50;

export function CorpusArticlesPanel({
  articles,
  busy,
  onIndex,
  onReindex,
  onDelete,
  onInspect,
  onViewNativeSource,
  onViewTables,
  onViewFigures,
}: CorpusArticlesPanelProps) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(articles.length / articlesPerPage));
  const visibleArticles = useMemo(() => {
    const start = (page - 1) * articlesPerPage;
    return articles.slice(start, start + articlesPerPage);
  }, [articles, page]);

  useEffect(() => {
    // Deletion or a new filter can shorten the list while a later page is open.
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  return (
    <Card className="scroll-mt-6 overflow-hidden" id="articles">
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-bold text-slate-900">Documents avec texte intégral</h2>
          <p className="mt-1 text-xs text-slate-500">PDF, métadonnées et couverture d’indexation</p>
        </div>
        <Button loading={busy === "index"} onClick={onIndex} variant="secondary">
          <RefreshCw aria-hidden="true" className="size-4" />
          Indexer les fragments
        </Button>
      </CardHeader>
      {articles.length === 0 ? (
        <CardBody>
          <EmptyState
            description="Importez un PDF scientifique pour créer les premiers fragments."
            icon={RefreshCw}
            title="Aucun PDF dans la base"
          />
        </CardBody>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-3 font-semibold">Article</th>
                <th className="px-4 py-3 font-semibold">Année</th>
                <th className="px-4 py-3 font-semibold">Statut</th>
                <th className="px-4 py-3 font-semibold">Index</th>
                <th className="px-4 py-3 font-semibold">DOI</th>
                <th className="sticky right-0 w-64 bg-slate-50 px-5 py-3 text-right font-semibold shadow-[-8px_0_12px_-12px_rgb(15_23_42_/_0.25)]">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleArticles.map((article) => (
                <CorpusArticleRow
                  article={article}
                  busy={busy}
                  key={article.id}
                  onDelete={onDelete}
                  onInspect={onInspect}
                  onReindex={onReindex}
                  onViewNativeSource={onViewNativeSource}
                  onViewTables={onViewTables}
                  onViewFigures={onViewFigures}
                />
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-4">
            <Button
              disabled={page === 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              variant="secondary"
            >
              Précédent
            </Button>
            <p aria-live="polite" className="text-xs font-semibold text-slate-500">
              Page {page} sur {pageCount} · {articles.length} document(s)
            </p>
            <Button
              disabled={page === pageCount}
              onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              variant="secondary"
            >
              Suivant
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function CorpusArticleRow({
  article,
  busy,
  onReindex,
  onDelete,
  onInspect,
  onViewNativeSource,
  onViewTables,
  onViewFigures,
}: {
  article: CorpusArticle;
  busy: string | null;
  onReindex: (article: CorpusArticle) => void;
  onDelete: (article: CorpusArticle) => void;
  onInspect: (article: CorpusArticle) => void;
  onViewNativeSource: (article: CorpusArticle) => void;
  onViewTables: (article: CorpusArticle) => void;
  onViewFigures: (article: CorpusArticle) => void;
}) {
  const indexed = article.indexed_chunk_count ?? 0;
  const coverage = Math.round((indexed / Math.max(article.chunk_count, 1)) * 100);
  return (
    <tr className="group transition hover:bg-forest-50/40">
      <td className="max-w-md px-5 py-4">
        <p className="font-semibold text-slate-900">{article.title}</p>
        <p className="mt-1 truncate text-xs text-slate-500">
          {article.journal ?? article.pdf_path}
        </p>
      </td>
      <td className="px-4 py-4 text-slate-600">{article.publication_year ?? "—"}</td>
      <td className="px-4 py-4">
        <Badge tone={statusTone(article.validation_status)}>{article.validation_status}</Badge>
      </td>
      <td className="px-4 py-4">
        <p className="font-semibold text-slate-700">
          {indexed}/{article.chunk_count}
        </p>
        <div
          aria-label={`${coverage}% des fragments indexés`}
          className="mt-2 h-1.5 w-24 overflow-hidden rounded-full bg-slate-100"
          role="progressbar"
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={coverage}
        >
          <div className="h-full rounded-full bg-forest-600" style={{ width: `${coverage}%` }} />
        </div>
      </td>
      <td className="max-w-44 truncate px-4 py-4 font-mono text-xs text-slate-500">
        {article.doi ?? "—"}
      </td>
      <td className="sticky right-0 bg-white px-5 py-4 shadow-[-8px_0_12px_-12px_rgb(15_23_42_/_0.35)] transition group-hover:bg-forest-50/40">
        <div className="flex justify-end gap-2">
          <Button
            aria-label={`Lire la source native de ${article.title}`}
            className="shrink-0"
            onClick={() => onViewNativeSource(article)}
            size="icon"
            title={`Lire la source native de ${article.title}`}
            variant="ghost"
          >
            <BookOpenText aria-hidden="true" className="size-4" />
            <span className="sr-only">Lire la source native</span>
          </Button>
          <Button
            aria-label={`Consulter les tableaux de ${article.title}`}
            className="shrink-0"
            onClick={() => onViewTables(article)}
            size="icon"
            title={`Consulter les tableaux de ${article.title}`}
            variant="ghost"
          >
            <Table2 aria-hidden="true" className="size-4" />
            <span className="sr-only">Consulter les tableaux</span>
          </Button>
          <Button
            aria-label={`Consulter les figures de ${article.title}`}
            className="shrink-0"
            onClick={() => onViewFigures(article)}
            size="icon"
            title={`Consulter les figures de ${article.title}`}
            variant="ghost"
          >
            <Image aria-hidden="true" className="size-4" />
            <span className="sr-only">Consulter les figures</span>
          </Button>
          <Button
            aria-label={`Inspecter ${article.title}`}
            className="shrink-0"
            onClick={() => onInspect(article)}
            size="icon"
            title={`Inspecter ${article.title}`}
            variant="ghost"
          >
            <Eye aria-hidden="true" className="size-4" />
            <span className="sr-only">Inspecter</span>
          </Button>
          <Button
            aria-label={`Réindexer ${article.title}`}
            className="shrink-0"
            loading={busy === `reindex-${article.id}`}
            onClick={() => onReindex(article)}
            size="icon"
            title={`R\u00e9indexer ${article.title}`}
            variant="ghost"
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            <span className="sr-only">{"R\u00e9indexer"}</span>
          </Button>
          <Button
            aria-label={`Supprimer ${article.title}`}
            className="shrink-0 text-red-600 hover:bg-red-50"
            onClick={() => onDelete(article)}
            size="icon"
            title={`Supprimer ${article.title}`}
            variant="ghost"
          >
            <Trash2 aria-hidden="true" className="size-4" />
            <span className="sr-only">Supprimer</span>
          </Button>
        </div>
      </td>
    </tr>
  );
}
