import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CorpusImportPanel } from "./CorpusImportPanel";

describe("CorpusImportPanel", () => {
  it("explains why a processed duplicate does not create another article", () => {
    const markup = renderToStaticMarkup(
      createElement(CorpusImportPanel, {
        files: [],
        folder: "",
        recursive: true,
        reports: [
          {
            pdf_path: "duplicate.pdf",
            sha256: "a".repeat(64),
            article_id: "existing-article",
            status: "duplicate",
            duplicate_reason: "normalized_text",
            page_count: 12,
            chunk_count: 34,
            element_count: 0,
            ocr_uncertain_page_count: 0,
            resumed_from_cache: false,
            requested_parser_id: "pymupdf",
            actual_parser_id: "pymupdf",
            extraction_run_id: null,
            warning_count: 0,
            error_type: null,
            error_message: null,
            duration_seconds: 0,
          },
        ],
        busy: null,
        onFilesChange: () => undefined,
        onFolderChange: () => undefined,
        onRecursiveChange: () => undefined,
        onUpload: () => undefined,
        onFolderIngest: () => undefined,
      }),
    );

    expect(markup).toContain("Déjà présent");
    expect(markup).toContain("Même texte intégral déjà enregistré");
    expect(markup).toContain("aucun nouvel article créé");
  });
});
