import type {
  ChatbotSource,
  ChatConversation,
  ChatConversationSummary,
  StoredChatResponse,
} from "@/types/api";

import type { ChatMessage } from "./chatSession";

export const welcomeMessage: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "Bonjour, je suis l’assistant scientifique de CiderScholar. Posez-moi une question sur la conception du cidre, sa biochimie, sa microbiologie, les polyphénols, les protéines, les jus ou les produits dérivés. Je répondrai uniquement à partir de sources identifiables.",
};

export const suggestions = [
  "Comment les polyphénols influencent-ils l’amertume et l’astringence du cidre ?",
  "Quel rôle jouent les levures non-Saccharomyces dans la formation des arômes ?",
  "Quels paramètres biochimiques faut-il suivre pendant une fermentation cidricole ?",
];

function normalizeStoredSource(source: ChatbotSource): ChatbotSource {
  return {
    ...source,
    evidence_level: source.evidence_level ?? "abstract",
    article_id: source.article_id ?? null,
    chunk_ids: source.chunk_ids ?? [],
    page_ranges: source.page_ranges ?? [],
    authors: source.authors ?? [],
    providers: source.providers ?? [],
  };
}

export function normalizeStoredResponse(response: StoredChatResponse): StoredChatResponse {
  if ("kind" in response) {
    return response;
  }
  return {
    ...response,
    sources: (response.sources ?? []).map(normalizeStoredSource),
    warnings: response.warnings ?? [],
    generation_status: response.generation_status ?? "generated",
    diagnostic_code: response.diagnostic_code ?? null,
    interaction_mode: response.interaction_mode ?? "research",
    reused_previous_sources: response.reused_previous_sources ?? false,
  };
}

export function toConversationMessages(conversation: ChatConversation): ChatMessage[] {
  return conversation.messages.map((message) => {
    const storedResponse = message.response ? normalizeStoredResponse(message.response) : undefined;
    return {
      id: message.id,
      role: message.role,
      content: message.content,
      ...(storedResponse && "kind" in storedResponse
        ? { terminalNotice: storedResponse }
        : storedResponse
          ? { response: storedResponse }
          : {}),
      ...(message.response_time_milliseconds !== null
        ? { responseTimeMilliseconds: message.response_time_milliseconds }
        : {}),
      helpful: message.helpful,
    };
  });
}

export function toSummary(conversation: ChatConversation): ChatConversationSummary {
  return {
    id: conversation.id,
    title: conversation.title,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
    message_count: conversation.message_count,
    last_message: conversation.last_message,
    active_job_count: conversation.active_job_count,
    favorite: conversation.favorite,
  };
}
