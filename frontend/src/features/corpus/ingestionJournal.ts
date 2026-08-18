import type { IngestionJob } from "@/types/api";

export const ingestionJournalPageSizes = [25, 50, 100] as const;

export type IngestionJournalPageSize = (typeof ingestionJournalPageSizes)[number];

const attentionStates = new Set(["failed", "ocr_required"]);

export function attentionIngestionJobs(jobs: IngestionJob[]): IngestionJob[] {
  return jobs.filter((job) => attentionStates.has(job.state));
}

export function visibleIngestionJobs(jobs: IngestionJob[], visibleCount: number): IngestionJob[] {
  return jobs.slice(0, visibleCount);
}

export function hasMoreIngestionJobs(jobs: IngestionJob[], visibleCount: number): boolean {
  return visibleCount < jobs.length;
}
