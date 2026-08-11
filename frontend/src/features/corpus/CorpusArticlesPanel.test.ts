import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CorpusArticle } from "@/types/api";

import { CorpusArticlesPanel } from "./CorpusArticlesPanel";

function article(index: number): CorpusArticle {
  return {
    id: `article-${index}`,
    title: `Article ${index}`,
    doi: null,
    journal: "Journal",
    work_type: "journal-article",
    publisher: null,
    publication_year: 2026,
    language: "fr",
    validation_status: "accepted",
    pdf_path: `article-${index}.pdf`,
    source: "test",
    created_at: "2026-07-29T00:00:00Z",
    indexed_at: null,
    chunk_count: 10,
    indexed_chunk_count: 10,
  };
}

describe("CorpusArticlesPanel pagination", () => {
  it("limits the initial render to fifty articles", () => {
    const markup = renderToStaticMarkup(
      createElement(CorpusArticlesPanel, {
        articles: Array.from({ length: 51 }, (_, index) => article(index)),
        busy: null,
        onDelete: () => undefined,
        onIndex: () => undefined,
        onReindex: () => undefined,
      }),
    );

    expect(markup).toContain("Article 49");
    expect(markup).not.toContain("Article 50");
    expect(markup).toContain("Page 1 sur 2");
    expect(markup).toContain("51 document(s)");
  });
});
