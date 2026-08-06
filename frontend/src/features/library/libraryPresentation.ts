import type { LibraryRecordFilters } from "@/lib/api";

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
