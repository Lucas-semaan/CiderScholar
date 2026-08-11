import { ChevronLeft, ChevronRight, FileText } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Form";
import { formatNumber } from "@/lib/cn";
import { statusTone } from "@/lib/status";
import type { LibraryRecord } from "@/types/api";

import {
  authorPreview,
  libraryStatusLabels,
  publicationSource,
  themeLabel,
} from "./libraryPresentation";

interface LibraryRecordListProps {
  records: LibraryRecord[];
  total: number;
  page: number;
  pageCount: number;
  pageSize: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (limit: number) => void;
}

export function LibraryRecordList({
  records,
  total,
  page,
  pageCount,
  pageSize,
  selectedId,
  onSelect,
  onPageChange,
  onPageSizeChange,
}: LibraryRecordListProps) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-bold text-slate-900">{formatNumber(total)} résultat(s)</h2>
          <p className="mt-1 text-xs text-slate-500">
            Page {page} sur {pageCount}
          </p>
        </div>
        <Select
          aria-label="Résultats par page"
          className="w-28"
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          value={pageSize}
        >
          {[25, 50, 100].map((value) => (
            <option key={value} value={value}>
              {value} / page
            </option>
          ))}
        </Select>
      </CardHeader>
      <div className="divide-y divide-slate-100">
        {records.map((record) => (
          <LibraryRecordRow
            key={record.library_id}
            onSelect={onSelect}
            record={record}
            selected={selectedId === record.library_id}
          />
        ))}
      </div>
      <div className="flex items-center justify-between border-t border-slate-100 px-5 py-4">
        <Button disabled={page <= 1} onClick={() => onPageChange(page - 1)} variant="secondary">
          <ChevronLeft className="size-4" /> Précédent
        </Button>
        <span aria-live="polite" className="text-xs font-semibold text-slate-500">
          {page} / {pageCount}
        </span>
        <Button
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
          variant="secondary"
        >
          Suivant <ChevronRight className="size-4" />
        </Button>
      </div>
    </Card>
  );
}

function LibraryRecordRow({
  record,
  selected,
  onSelect,
}: {
  record: LibraryRecord;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const authors = authorPreview(record.authors);
  const source = publicationSource(record);
  return (
    <button
      aria-label={`Consulter le document : ${record.title}`}
      aria-pressed={selected}
      className={
        selected
          ? "block w-full border-l-4 border-forest-600 bg-forest-50 px-5 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-forest-500"
          : "block w-full border-l-4 border-transparent px-5 py-4 text-left transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-forest-500"
      }
      onClick={() => onSelect(record.library_id)}
      type="button"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="font-semibold leading-6 text-slate-900">{record.title}</p>
          <p className="mt-1 text-xs text-slate-500">
            {source.value ?? `${source.label} inconnue`} ·{" "}
            {record.publication_year ?? "année inconnue"}
          </p>
          {authors && (
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">
              Auteurs : {authors}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Badge tone={record.document_type === "full_text" ? "info" : "neutral"}>
            {record.document_type === "full_text" ? (
              <>
                <FileText aria-hidden="true" className="size-3" /> Full article
              </>
            ) : (
              "Abstract only"
            )}
          </Badge>
          {record.relevance_status !== "accepted" && (
            <Badge tone={statusTone(record.relevance_status)}>
              {libraryStatusLabels[record.relevance_status]}
            </Badge>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {record.doi && <span className="font-mono">{record.doi}</span>}
        {(record.themes ?? (record.relevance_theme ? [record.relevance_theme] : []))
          .slice(0, 3)
          .map((theme) => (
            <Badge key={theme}>{themeLabel(theme)}</Badge>
          ))}
        <span>{record.abstract ? "Abstract disponible" : "Sans abstract"}</span>
        {record.document_type === "full_text" && (
          <span>
            {record.indexed_chunk_count}/{record.chunk_count} fragments indexés
          </span>
        )}
      </div>
    </button>
  );
}
