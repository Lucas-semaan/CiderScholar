import { createRef, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConversationTranscript } from "./ConversationTranscript";

describe("ConversationTranscript", () => {
  it("announces loading and enqueueing states from the conversation log", () => {
    const markup = renderToStaticMarkup(
      createElement(ConversationTranscript, {
        activeJobs: [],
        conversationLoading: true,
        endRef: createRef<HTMLDivElement>(),
        enqueueing: true,
        messages: [],
        onCancelJob: async () => undefined,
        onFeedback: async () => undefined,
        onRetryJob: async () => undefined,
      }),
    );

    expect(markup).toContain('role="log"');
    expect(markup).toContain("Chargement de la conversation…");
    expect(markup).toContain("Enregistrement de la demande…");
    expect(markup.match(/role="status"/g)).toHaveLength(1);
  });
});
