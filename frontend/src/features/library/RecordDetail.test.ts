import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RecordDetail, RecordDetailBody } from "@/features/library/RecordDetail";
import type { LibraryRecord } from "@/types/api";

const record: LibraryRecord = {
  id: "notice-1",
  library_id: "abstract:notice-1",
  canonical_key: "notice-1",
  doi: "10.1000/example",
  title: "Article source",
  abstract: "Résumé",
  authors: '["A. Auteur"]',
  journal: "Journal scientifique",
  work_type: "journal-article",
  publisher: null,
  publication_year: 2024,
  citation_count: 42,
  url: null,
  embedding_status: "not_applicable",
  relevance_status: "accepted",
  relevance_score: null,
  relevance_reason: null,
  relevance_theme: null,
  themes: [],
  sources: "openalex",
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

describe("RecordDetailBody", () => {
  it("does not show the bibliometric citation count in the source sidebar", () => {
    const markup = renderToStaticMarkup(
      createElement(RecordDetailBody, { onReviewed: () => undefined, record }),
    );

    expect(markup).not.toContain("Citations");
    expect(markup).not.toContain("42");
    expect(markup).toContain("Article de revue");
    expect(markup).toContain("Journal scientifique");
  });
});

describe("RecordDetail", () => {
  it("keeps the complete desktop detail in its own keyboard-accessible scroll region", () => {
    const markup = renderToStaticMarkup(
      createElement(RecordDetail, { onReviewed: () => undefined, record }),
    );

    expect(markup).toContain('role="region"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain("xl:max-h-[calc(100dvh-8rem)]");
    expect(markup).toContain("xl:overflow-y-auto");
    expect(markup).toContain("xl:overscroll-contain");
    expect(markup).not.toContain("max-h-72");
  });
});
