import type { IngestionReport } from "@/types/api";

function counted(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function ingestionOutcomeMessage(
  reports: IngestionReport[],
  expectedFileCount: number,
): string {
  if (reports.length === 0) {
    return `${expectedFileCount} PDF mis en file d’attente.`;
  }

  const added = reports.filter((report) => report.status === "chunks_ready").length;
  const duplicates = reports.filter((report) => report.status === "duplicate").length;
  const ocrRequired = reports.filter((report) => report.status === "ocr_required").length;
  const failed = reports.filter((report) => report.status === "failed").length;
  const outcomes = [
    added ? counted(added, "article ajouté", "articles ajoutés") : null,
    duplicates ? counted(duplicates, "déjà présent", "déjà présents") : null,
    ocrRequired ? `${ocrRequired} à vérifier par OCR` : null,
    failed ? counted(failed, "échec", "échecs") : null,
  ].filter((outcome): outcome is string => outcome !== null);

  return `${outcomes.join(" · ")}.`;
}
