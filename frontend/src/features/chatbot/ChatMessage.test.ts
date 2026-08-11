import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ChatMessage as ChatMessageValue } from "./chatSession";
import { ChatMessage } from "./ChatMessage";

describe("ChatMessage", () => {
  it("keeps facet drafts out of the user-facing response badges", () => {
    const message: ChatMessageValue = {
      id: "assistant-facets",
      role: "assistant",
      content: "Réponse sourcée",
      response: {
        message: "Réponse sourcée",
        retrieval_query: "fermentation",
        answer_markdown: "Réponse sourcée",
        sources: [],
        warnings: [],
        model: "argo",
        local_result_count: 2,
        external_result_count: 0,
        external_enrichment_used: false,
        prompt_tokens: 0,
        completion_tokens: 0,
        duration_seconds: 1,
        interaction_mode: "research",
        reused_previous_sources: false,
        facet_drafts: [
          {
            key: "aroma",
            label: "Arômes",
            query: "fermentation et arômes",
            answer_markdown: "Brouillon d'axe",
            cited_evidence_ids: [],
            source_record_ids: [],
          },
        ],
      },
    };

    const markup = renderToStaticMarkup(createElement(ChatMessage, { message }));

    expect(markup).not.toContain("Synthèse en 1 axe");
    expect(markup).toContain("RAG local");
  });
});
