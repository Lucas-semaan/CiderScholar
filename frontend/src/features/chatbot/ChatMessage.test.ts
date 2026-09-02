import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ChatMessage as ChatMessageValue } from "./chatSession";
import { ChatMessage } from "./ChatMessage";

describe("ChatMessage", () => {
  it("keeps local PDFs and DOI destinations distinct in cited sources", () => {
    const message: ChatMessageValue = {
      id: "assistant-local-pdf",
      role: "assistant",
      content: "Réponse sourcée",
      response: {
        message: "Réponse sourcée",
        retrieval_query: "fermentation",
        answer_markdown: "Réponse sourcée",
        sources: [
          {
            record_id: "common:article-1",
            origin: "local_rag",
            evidence_level: "full_text",
            article_id: "article-1",
            chunk_ids: [1],
            page_ranges: ["2"],
            title: "Article local",
            authors: [],
            doi: "10.1000/article-1",
            journal: null,
            publication_year: 2026,
            providers: ["corpus"],
            url: "https://doi.org/10.1000/article-1",
            local_pdf_url: "/api/corpus/article-1/pdf",
            snippet: "Preuve",
          },
        ],
        warnings: [],
        model: "argo",
        local_result_count: 1,
        external_result_count: 0,
        external_enrichment_used: false,
        prompt_tokens: 0,
        completion_tokens: 0,
        duration_seconds: 1,
        interaction_mode: "research",
        reused_previous_sources: false,
      },
    };

    const markup = renderToStaticMarkup(createElement(ChatMessage, { message }));

    expect(markup).toContain('href="/api/corpus/article-1/pdf"');
    expect(markup).toContain('href="https://doi.org/10.1000/article-1"');
    expect(markup).toContain("Ouvrir le PDF local de Article local");
  });

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

  it("explains cumulative scientific validation failures without exposing raw codes", () => {
    const message: ChatMessageValue = {
      id: "assistant-validation-failed",
      role: "assistant",
      content: "La synthèse n'a pas satisfait les contrôles scientifiques.",
      response: {
        message: "La synthèse n'a pas satisfait les contrôles scientifiques.",
        retrieval_query: "fermentation",
        answer_markdown: "La synthèse n'a pas satisfait les contrôles scientifiques.",
        sources: [],
        warnings: [],
        model: "deterministic-structured-fallback",
        local_result_count: 12,
        external_result_count: 0,
        external_enrichment_used: false,
        prompt_tokens: 33,
        completion_tokens: 11,
        duration_seconds: 1,
        generation_status: "validation_failed",
        diagnostic_code: "missing_required_evidence",
        diagnostic_codes: ["missing_required_evidence", "paragraph_too_short"],
        retrieval_traces: [
          {
            schema_version: 1,
            stage: "llm_context",
            query_variant_count: 0,
            vector_query_count: 0,
            cache_hit_count: 0,
            cache_miss_count: 0,
            lexical_candidate_count: 0,
            dense_candidate_count: 0,
            rrf_unique_candidate_count: 0,
            fused_candidate_count: 0,
            pre_rerank_candidate_count: 0,
            post_rerank_candidate_count: 0,
            selected_article_count: 3,
            selected_passage_count: 9,
            selected_full_text_article_count: 2,
            selected_full_text_passage_count: 8,
            selected_abstract_article_count: 1,
            selected_abstract_passage_count: 1,
            rejection_counts: {},
            vector_search_degraded: false,
          },
        ],
        interaction_mode: "research",
        reused_previous_sources: false,
      },
    };

    const markup = renderToStaticMarkup(createElement(ChatMessage, { message }));

    expect(markup).toContain("Synthèse non validée");
    expect(markup).toContain("Preuves pertinentes non toutes intégrées");
    expect(markup).toContain("Paragraphes insuffisamment développés");
    expect(markup).toContain("Documents retrouvés, non cités");
    expect(markup).toContain("2 texte(s) intégral(aux)");
    expect(markup).toContain("8 passage(s)");
    expect(markup).toContain("1 abstract(s)");
    expect(markup).not.toContain("missing_required_evidence");
  });
});
