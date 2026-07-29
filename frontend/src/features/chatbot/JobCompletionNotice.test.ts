import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DurableJob } from "@/types/api";

import { JobCompletionNotice } from "./JobCompletionNotice";

describe("JobCompletionNotice", () => {
  it("announces another conversation without requesting system permission", () => {
    const job: DurableJob = {
      id: "job-1",
      conversation_id: "conversation-2",
      type: "chat_answer",
      state: "succeeded",
      step: "persistence",
      attempt: 1,
      available_at: "2026-07-22T10:00:00Z",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:10Z",
      result_message_id: "message-2",
      error: null,
    };
    const markup = renderToStaticMarkup(
      createElement(JobCompletionNotice, {
        conversationTitle: "Fermentation lente",
        job,
        onDismiss: () => undefined,
        onOpen: () => undefined,
      }),
    );

    expect(markup).toContain('role="status"');
    expect(markup).toContain("La réponse est prête");
    expect(markup).toContain("Fermentation lente");
    expect(markup).toContain("Ouvrir la conversation");
  });
});
