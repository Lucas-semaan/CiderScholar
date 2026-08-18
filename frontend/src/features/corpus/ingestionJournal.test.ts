import { describe, expect, it } from "vitest";

import type { IngestionJob } from "@/types/api";

import {
  attentionIngestionJobs,
  hasMoreIngestionJobs,
  visibleIngestionJobs,
} from "./ingestionJournal";

function job(id: number): IngestionJob {
  return {
    id,
    pdf_path: `document-${id}.pdf`,
    sha256: null,
    state: "completed",
    article_id: null,
    error_type: null,
    error_message: null,
    attempt_count: 1,
    created_at: "2026-08-13T10:00:00Z",
    updated_at: "2026-08-13T10:00:00Z",
  };
}

describe("ingestion journal pagination", () => {
  const jobs = Array.from({ length: 101 }, (_, index) => job(index + 1));

  it("starts from one 25-item window", () => {
    expect(visibleIngestionJobs(jobs, 25)).toHaveLength(25);
    expect(hasMoreIngestionJobs(jobs, 25)).toBe(true);
  });

  it("shows successive items and hides the control once all jobs are visible", () => {
    expect(visibleIngestionJobs(jobs, 50).at(-1)?.id).toBe(50);
    expect(visibleIngestionJobs(jobs, 125)).toHaveLength(101);
    expect(hasMoreIngestionJobs(jobs, 125)).toBe(false);
  });

  it("keeps only failed and OCR-required operations in the attention view", () => {
    const attentionJobs = attentionIngestionJobs([
      { ...job(1), state: "completed" },
      { ...job(2), state: "ocr_required" },
      { ...job(3), state: "failed" },
    ]);

    expect(attentionJobs.map((item) => item.id)).toEqual([2, 3]);
  });
});
