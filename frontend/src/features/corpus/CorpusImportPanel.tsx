import { FolderOpen, UploadCloud } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Form";
import { statusTone } from "@/lib/status";
import type { IngestionReport } from "@/types/api";

interface CorpusImportPanelProps {
  files: File[];
  folder: string;
  recursive: boolean;
  reports: IngestionReport[];
  busy: string | null;
  onFilesChange: (files: File[]) => void;
  onFolderChange: (folder: string) => void;
  onRecursiveChange: (recursive: boolean) => void;
  onUpload: () => void;
  onFolderIngest: () => void;
}

export function CorpusImportPanel({
  files,
  folder,
  recursive,
  reports,
  busy,
  onFilesChange,
  onFolderChange,
  onRecursiveChange,
  onUpload,
  onFolderIngest,
}: CorpusImportPanelProps) {
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <h2 className="font-bold text-slate-900">Déposer des PDF</h2>
          <p className="mt-1 text-xs text-slate-500">
            Copie locale, extraction puis déduplication DOI
          </p>
        </CardHeader>
        <CardBody className="space-y-5">
          <label className="grid min-h-48 cursor-pointer place-items-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50 p-6 text-center transition hover:border-forest-300 hover:bg-forest-50 focus-within:ring-2 focus-within:ring-forest-500">
            <input
              accept="application/pdf"
              className="sr-only"
              multiple
              onChange={(event) => onFilesChange(Array.from(event.target.files ?? []))}
              type="file"
            />
            <span>
              <span className="mx-auto grid size-12 place-items-center rounded-2xl bg-white text-forest-700 shadow-sm">
                <UploadCloud aria-hidden="true" className="size-6" />
              </span>
              <span className="mt-4 block text-sm font-bold text-slate-800">
                {files.length ? `${files.length} fichier(s) sélectionné(s)` : "Choisir des PDF"}
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                Import séquentiel, sans OCR automatique
              </span>
            </span>
          </label>
          <Button disabled={files.length === 0} loading={busy === "upload"} onClick={onUpload}>
            Importer la sélection
          </Button>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <h2 className="font-bold text-slate-900">Analyser un dossier</h2>
          <p className="mt-1 text-xs text-slate-500">Chemin local explicitement autorisé</p>
        </CardHeader>
        <CardBody className="space-y-5">
          <Field label="Dossier contenant les PDF">
            <Input
              onChange={(event) => onFolderChange(event.target.value)}
              placeholder="C:\\Documents\\Articles"
              value={folder}
            />
          </Field>
          <label className="flex items-center gap-3 text-sm font-medium text-slate-700">
            <input
              checked={recursive}
              className="size-4 rounded border-slate-300 accent-forest-700"
              onChange={(event) => onRecursiveChange(event.target.checked)}
              type="checkbox"
            />
            Inclure les sous-dossiers
          </label>
          <Button disabled={!folder.trim()} loading={busy === "folder"} onClick={onFolderIngest}>
            <FolderOpen aria-hidden="true" className="size-4" />
            Analyser le dossier
          </Button>
        </CardBody>
      </Card>
      {reports.length > 0 && (
        <Card className="xl:col-span-2">
          <CardHeader>
            <h2 className="font-bold text-slate-900">Dernier traitement</h2>
          </CardHeader>
          <CardBody className="grid gap-3 md:grid-cols-2">
            {reports.map((report) => (
              <div
                className="rounded-xl border border-slate-100 bg-slate-50 p-4"
                key={report.pdf_path}
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="truncate text-sm font-semibold text-slate-800">{report.pdf_path}</p>
                  <Badge tone={statusTone(report.status)}>{report.status}</Badge>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  {report.page_count} page(s) · {report.chunk_count} fragment(s)
                </p>
              </div>
            ))}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
