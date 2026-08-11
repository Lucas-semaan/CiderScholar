import { describe, expect, it, vi } from "vitest";

import {
  appendPersistedUserMessage,
  acquireSubmissionLock,
  createPendingChatSubmission,
  enqueuePendingChat,
  loadConversationWithActiveJobs,
  reloadTerminalConversation,
  releaseSubmissionLock,
} from "./durableChat";

describe("durable chat submission", () => {
  it("reuses the same client request UUID after a network retry", async () => {
    const enqueue = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce({});
    const pending = createPendingChatSubmission(
      "Question durable",
      false,
      "11111111-1111-4111-8111-111111111111",
    );

    await expect(enqueuePendingChat("conversation-1", pending, enqueue)).rejects.toThrow(
      "network unavailable",
    );
    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue).toHaveBeenCalledTimes(2);
    expect(enqueue.mock.calls[0]?.[1].client_request_id).toBe(pending.clientRequestId);
    expect(enqueue.mock.calls[1]?.[1].client_request_id).toBe(pending.clientRequestId);
    expect(enqueue.mock.calls[1]?.[1].mode).toBe("quick");
    expect(enqueue.mock.calls[1]?.[1].interaction_mode).toBe("auto");
    expect(enqueue.mock.calls[1]?.[1].analyze_figures).toBe(false);
  });

  it("persists the selected deep-research mode across enqueue retries", async () => {
    const enqueue = vi.fn().mockResolvedValue({});
    const pending = createPendingChatSubmission(
      "Analyse approfondie",
      false,
      "22222222-2222-4222-8222-222222222222",
      true,
    );

    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue.mock.calls[0]?.[1].mode).toBe("deep_research");
    expect(enqueue.mock.calls[0]?.[1].interaction_mode).toBe("research");
    expect(enqueue.mock.calls[0]?.[1].use_external_sources).toBe(false);
  });

  it("persists the explicit conversation mode across enqueue retries", async () => {
    const enqueue = vi.fn().mockResolvedValue({});
    const pending = createPendingChatSubmission(
      "Reformule sous forme de liste",
      false,
      "33333333-3333-4333-8333-333333333333",
      false,
      "conversation",
    );

    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue.mock.calls[0]?.[1].interaction_mode).toBe("conversation");
  });

  it("persists the local figure-analysis choice across enqueue retries", async () => {
    const enqueue = vi.fn().mockResolvedValue({});
    const pending = createPendingChatSubmission(
      "Que montrent les figures ?",
      false,
      "44444444-4444-4444-8444-444444444444",
      false,
      "research",
      true,
    );

    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue.mock.calls[0]?.[1].analyze_figures).toBe(true);
  });

  it("preserves the selected answer intensity for conversation replies", async () => {
    const enqueue = vi.fn().mockResolvedValue({});
    const pending = createPendingChatSubmission(
      "Explique plus brièvement",
      false,
      "55555555-5555-4555-8555-555555555555",
      false,
      "conversation",
      false,
      "concise",
    );

    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue.mock.calls[0]?.[1].interaction_mode).toBe("conversation");
    expect(enqueue.mock.calls[0]?.[1].answer_effort).toBe("concise");
  });

  it("forces deep intensity when deep research is selected", async () => {
    const enqueue = vi.fn().mockResolvedValue({});
    const pending = createPendingChatSubmission(
      "Analyse approfondie",
      false,
      "66666666-6666-4666-8666-666666666666",
      true,
      "research",
      false,
      "concise",
    );

    await enqueuePendingChat("conversation-1", pending, enqueue);

    expect(enqueue.mock.calls[0]?.[1].answer_effort).toBe("deep");
  });

  it("renders the canonical persisted message exactly once", () => {
    const persisted = {
      id: "message-from-sqlite",
      role: "user" as const,
      content: "Question persistée",
      created_at: "2026-07-22T10:00:00Z",
    };

    const once = appendPersistedUserMessage([], persisted);
    const twice = appendPersistedUserMessage(once, persisted);

    expect(once).toEqual([
      {
        id: "message-from-sqlite",
        role: "user",
        content: "Question persistée",
      },
    ]);
    expect(twice).toBe(once);
  });

  it("reloads the persisted conversation after every terminal job outcome", async () => {
    const load = vi.fn().mockResolvedValue({ id: "conversation-1", messages: [] });
    const succeededJob = {
      id: "job-1",
      conversation_id: "conversation-1",
      type: "chat_answer" as const,
      state: "succeeded" as const,
      step: "persistence" as const,
      attempt: 1,
      available_at: "2026-07-22T10:00:00Z",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:02Z",
      result_message_id: "message-2",
      error: null,
    };

    await reloadTerminalConversation(succeededJob, load);
    await reloadTerminalConversation(
      {
        ...succeededJob,
        state: "failed",
        result_message_id: "failure-notice",
        error: { code: "validation", message: "Validation failed", retry_at: null },
      },
      load,
    );
    await reloadTerminalConversation(
      { ...succeededJob, state: "cancelled", result_message_id: "cancellation-notice" },
      load,
    );

    expect(load).toHaveBeenCalledTimes(3);
    expect(load).toHaveBeenCalledWith("conversation-1");
    await expect(
      reloadTerminalConversation({ ...succeededJob, state: "running" }, load),
    ).rejects.toThrow("before the job is terminal");
  });

  it("allows only one synchronous submit until the current enqueue settles", () => {
    const lock = { current: false };

    expect(acquireSubmissionLock(lock)).toBe(true);
    expect(acquireSubmissionLock(lock)).toBe(false);
    releaseSubmissionLock(lock);
    expect(acquireSubmissionLock(lock)).toBe(true);
  });

  it("restores an active job after reload without resubmitting the question", async () => {
    const activeJob = {
      id: "job-active",
      conversation_id: "conversation-1",
      type: "chat_answer" as const,
      state: "running" as const,
      step: "argo" as const,
      attempt: 1,
      available_at: "2026-07-22T10:00:00Z",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:01Z",
      result_message_id: null,
      error: null,
    };
    const load = vi.fn().mockResolvedValue({
      id: "conversation-1",
      title: "Question persistée",
      created_at: "2026-07-22T10:00:00Z",
      updated_at: "2026-07-22T10:00:01Z",
      message_count: 1,
      last_message: "Question persistée",
      active_job_count: 1,
      messages: [],
      active_jobs: [activeJob],
    });
    const trackJobs = vi.fn();
    const enqueue = vi.fn();

    await loadConversationWithActiveJobs("conversation-1", trackJobs, load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(trackJobs).toHaveBeenCalledWith([activeJob]);
    expect(enqueue).not.toHaveBeenCalled();
  });
});
