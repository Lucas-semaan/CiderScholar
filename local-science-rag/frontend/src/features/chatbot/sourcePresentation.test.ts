import { describe, expect, it } from "vitest";

import type { ChatbotSource } from "@/types/api";

import { sourceEvidenceLabel, sourceOriginLabel } from "./sourcePresentation";

function source(origin: ChatbotSource["origin"], scope: ChatbotSource["scope"]): ChatbotSource {
  return {
    record_id: `${origin}-${scope}`,
    origin,
    evidence_level: "abstract",
    scope,
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
  it("distinguishes common, private and live sources", () => {
    expect(sourceOriginLabel(source("local_rag", "common"))).toBe("Corpus commun");
    expect(sourceOriginLabel(source("local_rag", "private"))).toBe("Document privé");
    expect(sourceOriginLabel(source("external_api", null))).toBe("API en direct");
  });

  it("labels abstract and full-text evidence without ambiguity", () => {
    const abstractSource = source("local_rag", "common");
    const fullTextSource = { ...abstractSource, evidence_level: "full_text" as const };

    expect(sourceEvidenceLabel(abstractSource)).toBe("Preuve : abstract");
    expect(sourceEvidenceLabel(fullTextSource)).toBe("Preuve : texte intégral");
  });
});
