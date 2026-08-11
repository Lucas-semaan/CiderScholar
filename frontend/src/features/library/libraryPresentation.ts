import type { LibraryRecordFilters } from "@/lib/api";
import type { LibraryRecord } from "@/types/api";

export const libraryStatusLabels: Record<string, string> = {
  accepted: "Acceptée",
  review: "À réviser",
  unreviewed: "Non évaluée",
  rejected: "Rejetée",
};

export const initialLibraryFilters: LibraryRecordFilters = {
  query: "",
  statuses: ["accepted", "review", "unreviewed", "rejected"],
  theme: "",
  source: "",
  abstract: "all",
  availability: "all",
  limit: 25,
  offset: 0,
};

const themeLabels: Record<string, string> = {
  aromes_procede: "Arômes et procédés",
  biochimie: "Biochimie",
  calvados_eau_vie: "Calvados et eaux-de-vie",
  cidre: "Cidre",
  jus_pomme: "Jus de pomme",
  manual_istex: "Import manuel ISTEX",
  microbiologie: "Microbiologie",
  polyphenols: "Polyphénols",
  pommeau: "Pommeau",
  proteines: "Protéines",
};

export function themeLabel(theme: string): string {
  const normalized = theme
    .trim()
    .toLocaleLowerCase("fr-FR")
    .replace(/[\s-]+/g, "_");
  const translated = themeLabels[normalized];
  if (translated) return translated;

  const readable = theme.trim().replace(/_+/g, " ").replace(/\s+/g, " ");
  if (!readable) return "";
  return readable.charAt(0).toLocaleUpperCase("fr-FR") + readable.slice(1);
}

export function authorPreview(value: string): string {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed)) return "";
    const names = [
      ...new Set(
        parsed
          .map(String)
          .map((name) => name.trim())
          .filter(Boolean),
      ),
    ];
    const fullNames = names.filter((name) => {
      const parts = name.split(/\s+/);
      return !name.includes(",") && parts.length > 1 && parts.every((part) => part.length > 1);
    });
    const displayed = fullNames.length ? fullNames : names;
    const firstNames = displayed.slice(0, 5);
    return `${firstNames.join(", ")}${displayed.length > firstNames.length ? "…" : ""}`;
  } catch {
    return "";
  }
}

export function publicationSource(
  record: Pick<LibraryRecord, "journal" | "publisher" | "work_type">,
): {
  label: string;
  value: string | null;
} {
  const type = record.work_type?.trim().toLowerCase().replace(/[ _]/g, "-") ?? "";
  if (
    type.includes("book") ||
    type === "monograph" ||
    type === "reference-entry" ||
    type === "paratext"
  ) {
    return { label: "Éditeur", value: record.publisher ?? record.journal };
  }
  if (type.includes("proceedings") || type.includes("conference") || type === "presentation") {
    return { label: "Actes de conférence", value: record.journal ?? record.publisher };
  }
  if (type.includes("report") || type === "working-paper" || type === "grant") {
    return { label: "Institution éditrice", value: record.publisher ?? record.journal };
  }
  if (type === "thesis" || type === "dissertation") {
    return { label: "Établissement", value: record.publisher ?? record.journal };
  }
  if (type === "dataset" || type === "database") {
    return { label: "Dépôt de données", value: record.publisher ?? record.journal };
  }
  if (type === "preprint" || type === "posted-content") {
    return { label: "Plateforme de dépôt", value: record.journal ?? record.publisher };
  }
  if (type === "peer-review") {
    return { label: "Plateforme d’évaluation", value: record.journal ?? record.publisher };
  }
  if (type === "standard") {
    return { label: "Organisme de normalisation", value: record.publisher ?? record.journal };
  }
  if (type === "patent") {
    return {
      label: "Office de propriété intellectuelle",
      value: record.publisher ?? record.journal,
    };
  }
  if (type === "web-resource" || type === "blog" || type === "blog-post") {
    return { label: "Site", value: record.publisher ?? record.journal };
  }
  if (
    type === "journal-article" ||
    type === "journalarticle" ||
    type === "journal" ||
    type.startsWith("journal-") ||
    type === "article" ||
    type === "review" ||
    type === "editorial" ||
    type === "data-paper" ||
    type === "erratum" ||
    type === "retraction"
  ) {
    return { label: "Journal", value: record.journal ?? record.publisher };
  }
  return { label: "Publication", value: record.journal ?? record.publisher };
}

export function publicationTypeLabel(workType: string | null): string | null {
  const type = workType?.trim().toLowerCase().replace(/[ _]/g, "-") ?? "";
  if (!type) return null;
  if (type === "book-chapter" || type === "reference-entry") return "Chapitre d’ouvrage";
  if (type.includes("book") || type === "monograph" || type === "paratext") return "Ouvrage";
  if (type.includes("conference") || type.includes("proceedings")) {
    if (type.includes("poster")) return "Poster scientifique";
    return "Communication de conférence";
  }
  if (type === "presentation") return "Présentation";
  if (type === "supplementary-material") return "Matériel supplémentaire";
  if (type.includes("report") || type === "working-paper" || type === "grant") {
    return "Rapport";
  }
  if (type === "thesis" || type === "dissertation") return "Thèse ou mémoire";
  if (type === "dataset" || type === "database" || type === "data-paper") {
    return "Jeu de données";
  }
  if (type === "preprint" || type === "posted-content") return "Prépublication";
  if (type === "peer-review") return "Rapport d’évaluation";
  if (type === "standard") return "Norme";
  if (type === "patent") return "Brevet";
  if (type === "web-resource" || type === "blog" || type === "blog-post") {
    return "Ressource web";
  }
  if (
    type === "article" ||
    type === "review" ||
    type === "editorial" ||
    type === "erratum" ||
    type === "retraction" ||
    type === "journal" ||
    type.startsWith("journal-")
  ) {
    return "Article de revue";
  }
  return workType?.trim() || null;
}
