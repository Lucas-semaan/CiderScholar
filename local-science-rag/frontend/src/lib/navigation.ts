export type CorpusTab = "articles" | "import" | "activity";
export type LibrarySection = "records" | "pdf";

export const appDestinations = {
  scientificAssistant: "/",
  documentaryRecords: "/bibliotheque",
  localPdfs: "/bibliotheque?section=pdf&tab=articles",
  privateDocuments: "/bibliotheque?section=private&tab=articles",
  savedAnalyses: "/syntheses",
  corpusActivity: "/bibliotheque?section=pdf&tab=activity",
  privateCorpusActivity: "/bibliotheque?section=private&tab=activity",
} as const;

export function corpusDestinations(scope: "common" | "private") {
  if (scope === "private") {
    return {
      articles: `${appDestinations.privateDocuments}#articles`,
      assistant: appDestinations.scientificAssistant,
      activity: appDestinations.privateCorpusActivity,
    };
  }
  return {
    articles: `${appDestinations.localPdfs}#articles`,
    assistant: appDestinations.scientificAssistant,
    activity: appDestinations.corpusActivity,
  };
}

export function librarySectionFromQuery(value: string | null): LibrarySection {
  return value === "pdf" ? value : "records";
}

export function corpusTabFromQuery(value: string | null): CorpusTab {
  return value === "import" || value === "activity" ? value : "articles";
}
