import { describe, expect, it } from "vitest";

import { nextReviewRecordId } from "@/features/library/reviewQueue";
import type { LibraryRecord } from "@/types/api";

function record(id: string, relevance_status: LibraryRecord["relevance_status"]): LibraryRecord {
  return {
    id,
    library_id: `abstract:${id}`,
    canonical_key: id,
    doi: null,
    title: id,
    abstract: "Abstract",
    authors: "[]",
    journal: null,
    work_type: null,
    publisher: null,
    publication_year: null,
    citation_count: null,
    url: null,
    embedding_status: "not_applicable",
    relevance_status,
    relevance_score: null,
    relevance_reason: null,
    relevance_theme: null,
    themes: [],
    sources: null,
    first_seen_at: null,
    last_seen_at: null,
    document_type: "abstract_only",
    article_id: null,
    pdf_available: false,
    pdf_path: null,
    validation_status: null,
    chunk_count: 0,
    indexed_chunk_count: 0,
  };
}

describe("bibliographic review queue", () => {
  it("selects the following review notice", () => {
    const records = [
      record("accepted", "accepted"),
      record("one", "review"),
      record("two", "review"),
    ];

    expect(nextReviewRecordId(records, "one")).toBe("abstract:two");
  });

  it("wraps to an earlier review notice when needed", () => {
    const records = [
      record("one", "review"),
      record("accepted", "accepted"),
      record("two", "review"),
    ];

    expect(nextReviewRecordId(records, "two")).toBe("abstract:one");
  });

  it("returns null when no other review notice remains", () => {
    expect(nextReviewRecordId([record("one", "review")], "one")).toBeNull();
  });
});
