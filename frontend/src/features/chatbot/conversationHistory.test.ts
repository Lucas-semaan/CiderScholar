import { describe, expect, it } from "vitest";

import type { ChatConversationSummary } from "@/types/api";

import {
  activeJobBadgeLabel,
  createConversationTitle,
  groupConversations,
  withTrackedJobCounts,
} from "./conversationHistory";

function conversation(id: string, updatedAt: string): ChatConversationSummary {
  return {
    id,
    title: id,
    created_at: updatedAt,
    updated_at: updatedAt,
    message_count: 2,
    last_message: "Réponse",
    active_job_count: 0,
    favorite: false,
  };
}

describe("conversation history", () => {
  it("uses a concise first-question title", () => {
    expect(createConversationTitle("  Levures   et arômes  ")).toBe("Levures et arômes");
    expect(createConversationTitle("x".repeat(80))).toBe(`${"x".repeat(53)}…`);
  });

  it("groups conversations like a chat history", () => {
    const groups = groupConversations(
      [
        conversation("today", "2026-07-21T08:00:00Z"),
        conversation("yesterday", "2026-07-20T08:00:00Z"),
        conversation("week", "2026-07-16T08:00:00Z"),
        conversation("month", "2026-07-01T08:00:00Z"),
        conversation("old", "2026-05-01T08:00:00Z"),
      ],
      new Date("2026-07-21T12:00:00Z"),
    );

    expect(groups.map((group) => group.label)).toEqual([
      "Aujourd’hui",
      "Hier",
      "7 derniers jours",
      "30 derniers jours",
      "Plus ancien",
    ]);
  });

  it("counts only non-terminal tracked jobs for the badge", () => {
    const summary = conversation("conversation-1", "2026-07-21T08:00:00Z");
    const baseJob = {
      id: "job-1",
      conversation_id: summary.id,
      type: "chat_answer" as const,
      state: "running" as const,
      step: "argo" as const,
      attempt: 1,
      available_at: summary.updated_at,
      created_at: summary.updated_at,
      updated_at: summary.updated_at,
      result_message_id: null,
      error: null,
    };

    expect(withTrackedJobCounts([summary], [baseJob])[0]?.active_job_count).toBe(1);
    expect(
      withTrackedJobCounts([summary], [{ ...baseJob, state: "failed" }])[0]?.active_job_count,
    ).toBe(0);
    expect(activeJobBadgeLabel(2)).toBe("2 travaux en cours");
  });
});
