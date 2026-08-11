import { describe, expect, it } from "vitest";

import type { IngestionReport } from "@/types/api";

import { ingestionOutcomeMessage } from "./ingestionSummary";

function report(status: IngestionReport["status"]): IngestionReport {
  return {
    pdf_path: `${status}.pdf`,
    sha256: null,
    article_id: null,
    status,
    duplicate_reason: status === "duplicate" ? "sha256" : null,
    page_count: 1,
    chunk_count: 1,
    resumed_from_cache: false,
    error_type: null,
    error_message: null,
    duration_seconds: 0,
  };
}

describe("ingestionOutcomeMessage", () => {
  it("distinguishes created articles from already present PDFs", () => {
    expect(ingestionOutcomeMessage([report("chunks_ready"), report("duplicate")], 2)).toBe(
      "1 article ajouté · 1 déjà présent.",
    );
  });

  it("reports queued files when ingestion is asynchronous", () => {
    expect(ingestionOutcomeMessage([], 2)).toBe("2 PDF mis en file d’attente.");
  });
});
