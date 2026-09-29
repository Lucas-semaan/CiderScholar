import { useCallback, useMemo, useState } from "react";

import { BookOpenText, CheckCircle2, ClipboardList, Database, FileText } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Dialog } from "@/components/ui/Dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/Feedback";
import { MetricCard } from "@/components/ui/MetricCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { CorpusPage } from "@/features/corpus/CorpusPage";
import { LibraryFilters } from "@/features/library/LibraryFilters";
import { LibraryRecordList } from "@/features/library/LibraryRecordList";
import {
  RecordDetail,
  RecordDetailBody,
  RecordDetailHeader,
} from "@/features/library/RecordDetail";
import {
  acquisitionLibraryFilters,
  initialLibraryFilters,
} from "@/features/library/libraryPresentation";
import { nextReviewRecordId } from "@/features/library/reviewQueue";
import {
  librarySplitViewMediaQuery,
  shouldOpenLibraryDetailDialog,
} from "@/features/library/libraryDetail";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api, type LibraryRecordFilters } from "@/lib/api";
import { formatNumber } from "@/lib/cn";
import { librarySectionFromQuery } from "@/lib/navigation";

const librarySections = [
  { id: "records" as const, label: "Tous les documents", icon: BookOpenText },
  { id: "acquisition" as const, label: "Notices à acquérir", icon: ClipboardList },
  { id: "pdf" as const, label: "Imports et indexation", icon: Database },
];

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const section = librarySectionFromQuery(searchParams.get("section"));
  const selectSection = (nextSection: "records" | "acquisition" | "pdf") =>
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (nextSection === "pdf") {
        next.set("section", nextSection);
        if (!next.has("tab")) next.set("tab", "articles");
      } else if (nextSection === "acquisition") {
        next.set("section", nextSection);
        next.delete("tab");
      } else {
        next.delete("section");
        next.delete("tab");
      }
      return next;
    });

  return (
    <div className="space-y-6">
      <nav
        aria-label="Vues de la base documentaire"
        className="flex gap-1 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm"
      >
        {librarySections.map(({ id, label, icon: Icon }) => (
          <button
            aria-current={section === id ? "page" : undefined}
            className={
              section === id
                ? "flex min-h-11 items-center gap-2 whitespace-nowrap rounded-xl bg-forest-600 px-4 text-sm font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
                : "flex min-h-11 items-center gap-2 whitespace-nowrap rounded-xl px-4 text-sm font-semibold text-slate-600 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
            }
            key={id}
            onClick={() => selectSection(id)}
            type="button"
          >
            <Icon aria-hidden="true" className="size-4" />
            {label}
          </button>
        ))}
      </nav>
      {section === "pdf" ? (
        <CorpusPage embedded />
      ) : (
        <BibliographicLibrary
          key={section}
          mode={section === "acquisition" ? "acquisition" : "documents"}
        />
      )}
    </div>
  );
}

function BibliographicLibrary({ mode }: { mode: "documents" | "acquisition" }) {
  const startingFilters =
    mode === "acquisition" ? acquisitionLibraryFilters : initialLibraryFilters;
  // Editing filters does not issue a request until the user applies them.
  const [draft, setDraft] = useState<LibraryRecordFilters>({ ...startingFilters });
  const [applied, setApplied] = useState<LibraryRecordFilters>({ ...startingFilters });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewNotice, setReviewNotice] = useState<string | null>(null);
  const splitViewVisible = useMediaQuery(librarySplitViewMediaQuery);
  const summary = useRemoteData(useCallback(() => api.library.summary(), []));
  const records = useRemoteData(useCallback(() => api.library.records(applied), [applied]));
  const explicitlySelected = useMemo(
    () => records.data?.records.find((record) => record.library_id === selectedId) ?? null,
    [records.data, selectedId],
  );
  // Keep the review queue usable when a selected record leaves the current page.
  const selected =
    explicitlySelected ??
    records.data?.records.find((record) => record.relevance_status === "review") ??
    records.data?.records[0] ??
    null;
  const detailDialogOpen = shouldOpenLibraryDetailDialog(
    explicitlySelected !== null,
    splitViewVisible,
  );
  const handleReviewed = (message: string, recordId: string) => {
    setReviewNotice(message);
    setSelectedId(nextReviewRecordId(records.data?.records ?? [], recordId));
    summary.refresh();
    records.refresh();
  };
  const changePage = (nextPage: number) => {
    const offset = (nextPage - 1) * applied.limit;
    setApplied((previous) => ({ ...previous, offset }));
    setDraft((previous) => ({ ...previous, offset }));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const changePageSize = (limit: number) => {
    setDraft((previous) => ({ ...previous, limit, offset: 0 }));
    setApplied((previous) => ({ ...previous, limit, offset: 0 }));
  };

  if (summary.loading && !summary.data)
    return <LoadingState label="Ouverture de la base documentaire…" />;
  if (summary.error && !summary.data)
    return <ErrorState message={summary.error} retry={summary.refresh} />;
  if (!summary.data) return null;
  const total = records.data?.total ?? 0;
  const page = Math.floor(applied.offset / applied.limit) + 1;
  const pageCount = Math.max(Math.ceil(total / applied.limit), 1);
  const statistics = summary.data.statistics;
  const acquisitionMode = mode === "acquisition";

  return (
    <div className="space-y-8">
      <PageHeader
        description={
          acquisitionMode
            ? "Références conservées sans abstract ni texte intégral associé. Elles restent hors du RAG jusqu’à l’acquisition et l’indexation d’un contenu scientifique utilisable."
            : "Recherchez au même endroit les articles complets et les notices disposant d’un abstract. Lorsqu’un texte intégral correspondant est disponible, il remplace la fiche abstract seule."
        }
        eyebrow={acquisitionMode ? "File d’acquisition documentaire" : "Corpus scientifique unifié"}
        title={acquisitionMode ? "Notices à acquérir" : "Base documentaire"}
      />
      {reviewNotice && (
        <div
          className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800"
          role="status"
        >
          {reviewNotice}
        </div>
      )}
      {acquisitionMode ? (
        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard
            icon={ClipboardList}
            label="Notices à acquérir"
            note="Sans abstract ni texte intégral associé"
            tone="cider"
            value={formatNumber(statistics.acquisition_notices)}
          />
          <MetricCard
            icon={CheckCircle2}
            label="Acceptées sans contenu"
            note="Pertinence validée, contenu encore indisponible"
            value={formatNumber(statistics.accepted_without_content)}
          />
          <MetricCard
            icon={BookOpenText}
            label="À réviser sans contenu"
            note="Pertinence à confirmer avant acquisition"
            tone="slate"
            value={formatNumber(statistics.review_without_content)}
          />
        </section>
      ) : (
        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard
            icon={Database}
            label="Documents"
            note="Une seule entrée par identité bibliographique"
            value={formatNumber(statistics.documents)}
          />
          <MetricCard
            icon={FileText}
            label="Full article"
            note="PDF complet disponible et consultable"
            tone="sky"
            value={formatNumber(statistics.full_texts)}
          />
          <MetricCard
            icon={BookOpenText}
            label="Abstract only"
            note="Abstract disponible sans texte intégral"
            value={formatNumber(statistics.abstract_only)}
          />
        </section>
      )}
      <LibraryFilters
        filters={draft}
        mode={mode}
        onChange={setDraft}
        onSubmit={() => setApplied({ ...draft, offset: 0 })}
        themes={summary.data.filters.themes}
      />
      {records.error && <ErrorState message={records.error} retry={records.refresh} />}
      {records.loading && !records.data ? (
        <LoadingState label="Recherche dans la base documentaire…" />
      ) : records.data?.records.length === 0 ? (
        <EmptyState
          description={
            acquisitionMode
              ? "Aucune notice sans contenu ne correspond aux filtres sélectionnés."
              : "Élargissez les filtres ou essayez un autre mot-clé, titre ou DOI."
          }
          icon={acquisitionMode ? ClipboardList : BookOpenText}
          title={acquisitionMode ? "Aucune notice à acquérir" : "Aucun document trouvé"}
        />
      ) : records.data ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,.55fr)]">
          <LibraryRecordList
            onPageChange={changePage}
            onPageSizeChange={changePageSize}
            onSelect={setSelectedId}
            page={page}
            pageCount={pageCount}
            pageSize={applied.limit}
            records={records.data.records}
            selectedId={selected?.library_id ?? null}
            total={total}
          />
          {selected && (
            <div className="hidden xl:block">
              <RecordDetail onReviewed={handleReviewed} record={selected} />
            </div>
          )}
          <div className="xl:hidden">
            <Dialog
              onClose={() => setSelectedId(null)}
              open={detailDialogOpen}
              title="Document scientifique"
            >
              {explicitlySelected && (
                <div className="space-y-6">
                  <RecordDetailHeader record={explicitlySelected} />
                  <RecordDetailBody onReviewed={handleReviewed} record={explicitlySelected} />
                </div>
              )}
            </Dialog>
          </div>
        </div>
      ) : null}
    </div>
  );
}
