import { describe, expect, it } from "vitest";

import type { ChatbotSource } from "@/types/api";

import { sourceEvidenceLabel, sourceOriginLabel } from "./sourcePresentation";

function source(origin: ChatbotSource["origin"]): ChatbotSource {
  return {
    record_id: origin,
    origin,
    evidence_level: "abstract",
    article_id: null,
    chunk_ids: [],
    page_ranges: [],
    title: "Source",
    authors: [],
    doi: null,
    journal: null,
    publication_year: null,
    providers: [],
    url: null,
    snippet: "Preuve",
  };
}

describe("sourceOriginLabel", () => {
  it("distinguishes corpus and live sources", () => {
    expect(sourceOriginLabel(source("local_rag"))).toBe("Corpus commun");
    expect(sourceOriginLabel(source("external_api"))).toBe("API en direct");
  });

  it("labels abstract and full-text evidence without ambiguity", () => {
    const abstractSource = source("local_rag");
    const fullTextSource = { ...abstractSource, evidence_level: "full_text" as const };

    expect(sourceEvidenceLabel(abstractSource)).toBe("Preuve : abstract");
    expect(sourceEvidenceLabel(fullTextSource)).toBe("Preuve : texte intégral");
  });
});
