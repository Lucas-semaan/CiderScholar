import { describe, expect, it } from "vitest";

import type { ChatConversation } from "@/types/api";

import { toConversationMessages } from "./conversationView";

describe("stored conversation compatibility", () => {
  it("fills source fields that are absent from legacy chat responses", () => {
    const legacyConversation = {
      id: "conversation-legacy",
      title: "Conversation historique",
      created_at: "2026-07-21T10:00:00Z",
      updated_at: "2026-07-21T10:01:00Z",
      message_count: 1,
      last_message: "Réponse historique",
      active_job_count: 0,
      favorite: false,
      active_jobs: [],
      messages: [
        {
          id: "message-legacy",
          role: "assistant",
          content: "Réponse historique",
          response: {
            message: "Réponse historique",
            retrieval_query: "cidre",
            answer_markdown: "Réponse historique",
            sources: [
              {
                record_id: "legacy-source",
                origin: "local_rag",
                title: "Source historique",
                doi: null,
                journal: null,
                publication_year: 2020,
                providers: ["OpenAlex"],
                url: null,
                snippet: "Preuve historique",
              },
            ],
            warnings: [],
            model: "historical-model",
            local_result_count: 1,
            external_result_count: 0,
            external_enrichment_used: false,
            prompt_tokens: 0,
            completion_tokens: 0,
            duration_seconds: 1,
          },
          response_time_milliseconds: 1000,
          created_at: "2026-07-21T10:01:00Z",
          helpful: null,
        },
      ],
    } as unknown as ChatConversation;

    const [message] = toConversationMessages(legacyConversation);
    const source = message?.response?.sources[0];

    expect(message?.response?.interaction_mode).toBe("research");
    expect(message?.response?.reused_previous_sources).toBe(false);
    expect(source).toMatchObject({
      evidence_level: "abstract",
      article_id: null,
      chunk_ids: [],
      page_ranges: [],
      authors: [],
      providers: ["OpenAlex"],
    });
  });

  it("keeps a terminal job notice visible as an assistant message", () => {
    const conversation = {
      id: "conversation-failed",
      title: "Evaluation bloquee",
      created_at: "2026-08-06T01:00:00Z",
      updated_at: "2026-08-06T01:01:00Z",
      message_count: 1,
      last_message: "Reponse non produite.",
      active_job_count: 0,
      favorite: false,
      active_jobs: [],
      messages: [
        {
          id: "message-failed",
          role: "assistant",
          content: "**Reponse non produite.** La validation scientifique a echoue.",
          response: {
            kind: "job_terminal_notice",
            job_id: "job-failed",
            state: "failed",
            error_code: "scientific_validation_failed",
            diagnostic_code: "invalid_schema",
          },
          response_time_milliseconds: null,
          created_at: "2026-08-06T01:01:00Z",
          helpful: null,
        },
      ],
    } as ChatConversation;

    const [message] = toConversationMessages(conversation);

    expect(message?.response).toBeUndefined();
    expect(message?.terminalNotice).toEqual({
      kind: "job_terminal_notice",
      job_id: "job-failed",
      state: "failed",
      error_code: "scientific_validation_failed",
      diagnostic_code: "invalid_schema",
    });
  });
});
