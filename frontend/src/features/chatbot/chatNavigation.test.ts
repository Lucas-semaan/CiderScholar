import { describe, expect, it } from "vitest";

import type { DurableJob } from "@/types/api";

import {
  conversationNavigationDisabled,
  jobsForConversation,
  terminalJobDisposition,
} from "./chatNavigation";
import { pollDurableJob } from "./jobPolling";

const runningJob: DurableJob = {
  id: "job-1",
  conversation_id: "conversation-1",
  type: "chat_answer",
  state: "running",
  step: "argo",
  attempt: 1,
  available_at: "2026-07-22T10:00:00Z",
  created_at: "2026-07-22T10:00:00Z",
  updated_at: "2026-07-22T10:00:00Z",
  result_message_id: null,
  error: null,
};

describe("chat navigation during durable work", () => {
  it("does not disable navigation because a job is active", () => {
    expect(
      conversationNavigationDisabled({
        enqueueing: false,
        conversationLoading: false,
        trackedJobs: [runningJob],
      }),
    ).toBe(false);
  });

  it("keeps the old job attached when a new conversation is opened", () => {
    expect(jobsForConversation([runningJob], null)).toEqual([]);
    expect(jobsForConversation([runningJob], "conversation-1")).toEqual([runningJob]);
  });

  it("routes a simulated completed generation back to its original conversation", async () => {
    let selectedConversationId = "conversation-1";
    const succeededJob: DurableJob = {
      ...runningJob,
      state: "succeeded",
      step: "persistence",
      result_message_id: "answer-in-conversation-1",
    };
    const polling = pollDurableJob(runningJob, {
      poll: async () => {
        selectedConversationId = "conversation-2";
        return succeededJob;
      },
      wait: async () => undefined,
      onUpdate: () => undefined,
    });

    const completed = await polling;

    expect(selectedConversationId).toBe("conversation-2");
    expect(terminalJobDisposition(completed, selectedConversationId)).toBe("notify_other");
    expect(completed.conversation_id).toBe("conversation-1");
    expect(completed.result_message_id).toBe("answer-in-conversation-1");
  });
});
