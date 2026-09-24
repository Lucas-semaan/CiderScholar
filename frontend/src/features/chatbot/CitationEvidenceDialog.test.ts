import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ChatbotCitationAnchor } from "@/types/chat";

import { CitationEvidenceDialog } from "./CitationEvidenceDialog";

describe("CitationEvidenceDialog", () => {
  it("opens the persisted source at the first cited page", () => {
    const anchor: ChatbotCitationAnchor = {
      citation_id: "cite-0123456789abcdef",
      display_index: 1,
      label: "(Ascocid — Fermentation.docx, p. 4)",
      record_id: "common:ascocid",
      source_family: "ascocid_knowledge",
      article_id: "ascocid",
      title: "Fermentation.docx",
      evidence: [
        {
          evidence_id: "common:ascocid:chunk:7",
          snippet: "La gamme optimale se situe entre 6 et 10 °C.",
          chunk_id: 7,
          section: "Température",
          page_start: 4,
          page_end: 4,
          section_path: null,
          paragraph_start: null,
          paragraph_end: null,
          figure_label: null,
        },
      ],
      local_pdf_url: "/api/corpus/ascocid/pdf",
      source_url: null,
    };

    const markup = renderToStaticMarkup(
      createElement(CitationEvidenceDialog, {
        anchor,
        onClose: () => undefined,
        returnFocus: null,
      }),
    );

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("Livre AsCoCid");
    expect(markup).toContain("La gamme optimale se situe entre 6 et 10 °C.");
    expect(markup).toContain('href="/api/corpus/ascocid/pdf#page=4"');
  });
});
