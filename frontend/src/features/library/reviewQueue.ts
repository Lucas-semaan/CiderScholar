import type { LibraryRecord } from "@/types/api";

export function nextReviewRecordId(
  records: LibraryRecord[],
  reviewedRecordId: string,
): string | null {
  if (records.length < 2) return null;
  const currentIndex = records.findIndex((record) => record.id === reviewedRecordId);
  const startingIndex = currentIndex >= 0 ? currentIndex : -1;
  for (let step = 1; step <= records.length; step += 1) {
    const candidate = records[(startingIndex + step) % records.length];
    if (candidate && candidate.id !== reviewedRecordId && candidate.relevance_status === "review") {
      return candidate.library_id;
    }
  }
  return null;
}
