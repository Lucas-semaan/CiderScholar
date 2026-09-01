export type CorpusTab = "articles" | "import" | "activity";
export type LibrarySection = "records" | "acquisition" | "pdf";

export const appDestinations = {
  scientificAssistant: "/",
  documentaryRecords: "/bibliotheque",
  localPdfs: "/bibliotheque?section=pdf&tab=articles",
  savedAnalyses: "/syntheses",
  corpusActivity: "/bibliotheque?section=pdf&tab=activity&filter=attention",
} as const;

export function corpusDestinations() {
  return {
    articles: `${appDestinations.localPdfs}#articles`,
    assistant: appDestinations.scientificAssistant,
    activity: appDestinations.corpusActivity,
  };
}

export function librarySectionFromQuery(value: string | null): LibrarySection {
  return value === "acquisition" || value === "pdf" ? value : "records";
}

export function corpusTabFromQuery(value: string | null): CorpusTab {
  return value === "import" || value === "activity" ? value : "articles";
}
