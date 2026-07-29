import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { ChatConversation, ChatJobSubmitResponse, DurableJob } from "@/types/api";

import { terminalJobDisposition } from "./chatNavigation";
import {
  createPendingChatSubmission,
  enqueuePendingChat,
  loadConversationWithActiveJobs,
} from "./durableChat";
import { pollDurableJob } from "./jobPolling";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("durable chatbot flow", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("enqueues once, resumes after reload and finishes in the original chat", async () => {
    const queuedJob: DurableJob = {
      id: "job-1",
      conversation_id: "conversation-1",
      type: "chat_answer",
      state: "queued",
      step: "waiting",
      attempt: 0,
      available_at: "2026-07-22T10:00:00Z",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:00Z",
      result_message_id: null,
      error: null,
    };
    const runningJob: DurableJob = { ...queuedJob, state: "running", step: "argo", attempt: 1 };
    const succeededJob: DurableJob = {
      ...runningJob,
      state: "succeeded",
      step: "persistence",
      result_message_id: "assistant-1",
    };
    const accepted: ChatJobSubmitResponse = {
      job: queuedJob,
      user_message: {
        id: "user-1",
        role: "user",
        content: "Pourquoi cette fermentation ralentit-elle ?",
        created_at: "2026-07-22T10:00:00Z",
      },
    };
    const activeConversation: ChatConversation = {
      id: "conversation-1",
      title: "Fermentation lente",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:01Z",
      message_count: 1,
      last_message: accepted.user_message.content,
      active_job_count: 1,
      favorite: false,
      messages: [],
      active_jobs: [runningJob],
    };
    const completedConversation: ChatConversation = {
      ...activeConversation,
      updated_at: "2026-07-22T10:00:10Z",
      message_count: 2,
      active_job_count: 0,
      last_message: "Réponse persistée",
      active_jobs: [],
      messages: [
        {
          id: "assistant-1",
          role: "assistant",
          content: "Réponse persistée",
          response: null,
          response_time_milliseconds: 9_000,
          created_at: "2026-07-22T10:00:10Z",
          helpful: null,
        },
      ],
    };
    let conversationReads = 0;
    let enqueueRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        if (url.endsWith("/conversation-1/jobs") && init?.method === "POST") {
          enqueueRequests += 1;
          return jsonResponse(accepted, 202);
        }
        if (url === "/api/chatbot/conversations/conversation-1") {
          conversationReads += 1;
          return jsonResponse(conversationReads === 1 ? activeConversation : completedConversation);
        }
        if (url === "/api/jobs/job-1") return jsonResponse(succeededJob);
        return jsonResponse({ detail: "Unexpected request" }, 500);
      }),
    );

    const pending = createPendingChatSubmission(
      accepted.user_message.content,
      false,
      "11111111-1111-4111-8111-111111111111",
    );
    await enqueuePendingChat("conversation-1", pending);
    const restoredJobs: DurableJob[] = [];
    await loadConversationWithActiveJobs("conversation-1", (jobs) => restoredJobs.push(...jobs));
    let selectedConversationId = "conversation-2";
    const completed = await pollDurableJob(restoredJobs[0] ?? runningJob, {
      poll: api.jobs.poll,
      wait: async () => undefined,
      onUpdate: () => undefined,
    });

    expect(terminalJobDisposition(completed, selectedConversationId)).toBe("notify_other");
    selectedConversationId = "conversation-1";
    const originalChat = await loadConversationWithActiveJobs(
      selectedConversationId,
      () => undefined,
    );
    expect(enqueueRequests).toBe(1);
    expect(originalChat.messages[0]?.content).toBe("Réponse persistée");
    expect(completed.conversation_id).toBe("conversation-1");
  });
});
