import { describe, expect, it } from "vitest";

import { nextReviewRecordId } from "@/features/library/reviewQueue";
import type { LibraryRecord } from "@/types/api";

function record(id: string, relevance_status: LibraryRecord["relevance_status"]): LibraryRecord {
  return {
    id,
    canonical_key: id,
    doi: null,
    title: id,
    abstract: "Abstract",
    authors: "[]",
    journal: null,
    publication_year: null,
    citation_count: null,
    url: null,
    embedding_status: "not_applicable",
    relevance_status,
    relevance_score: null,
    relevance_reason: null,
    relevance_theme: null,
    sources: null,
    first_seen_at: null,
    last_seen_at: null,
  };
}

describe("bibliographic review queue", () => {
  it("selects the following review notice", () => {
    const records = [
      record("accepted", "accepted"),
      record("one", "review"),
      record("two", "review"),
    ];

    expect(nextReviewRecordId(records, "one")).toBe("two");
  });

  it("wraps to an earlier review notice when needed", () => {
    const records = [
      record("one", "review"),
      record("accepted", "accepted"),
      record("two", "review"),
    ];

    expect(nextReviewRecordId(records, "two")).toBe("one");
  });

  it("returns null when no other review notice remains", () => {
    expect(nextReviewRecordId([record("one", "review")], "one")).toBeNull();
  });
});
