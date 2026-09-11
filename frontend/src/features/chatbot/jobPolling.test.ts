import { afterEach, describe, expect, it, vi } from "vitest";

import type { DurableJob } from "@/types/api";

import { abortClientPolling, pollDurableJob } from "./jobPolling";

function makeJob(state: DurableJob["state"]): DurableJob {
  return {
    id: "job-1",
    conversation_id: "conversation-1",
    type: "chat_answer",
    state,
    step: state === "succeeded" ? "persistence" : "waiting",
    attempt: 1,
    available_at: "2026-07-22T10:00:00Z",
    created_at: "2026-07-22T10:00:00Z",
    updated_at: "2026-07-22T10:00:00Z",
    result_message_id: state === "succeeded" ? "message-2" : null,
    error: null,
  };
}

describe("pollDurableJob", () => {
  afterEach(() => vi.useRealTimers());

  it("releases abort listeners after every completed polling delay", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, "addEventListener");
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const poll = vi
      .fn()
      .mockResolvedValueOnce(makeJob("running"))
      .mockResolvedValueOnce(makeJob("running"))
      .mockResolvedValueOnce(makeJob("succeeded"));
    const polling = pollDurableJob(makeJob("queued"), {
      poll,
      onUpdate: vi.fn(),
      signal: controller.signal,
    });

    await vi.runAllTimersAsync();
    await expect(polling).resolves.toMatchObject({ state: "succeeded" });
    expect(add).toHaveBeenCalledTimes(3);
    expect(remove).toHaveBeenCalledTimes(3);
    for (const [event, listener] of add.mock.calls) {
      expect(remove).toHaveBeenCalledWith(event, listener);
    }
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels a pending delay immediately without issuing a network request", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const poll = vi.fn();
    const onUpdate = vi.fn();
    const polling = pollDurableJob(makeJob("queued"), {
      poll,
      onUpdate,
      signal: controller.signal,
    });
    const rejection = expect(polling).rejects.toBe("view closed");

    controller.abort("view closed");

    await rejection;
    expect(vi.getTimerCount()).toBe(0);
    expect(poll).not.toHaveBeenCalled();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("stops immediately after a terminal response", async () => {
    const poll = vi.fn().mockResolvedValue(makeJob("succeeded"));
    const wait = vi.fn().mockResolvedValue(undefined);
    const onUpdate = vi.fn();

    const result = await pollDurableJob(makeJob("queued"), { poll, wait, onUpdate });

    expect(result.state).toBe("succeeded");
    expect(poll).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledTimes(1);
  });

  it("honours its polling bound while a job remains active", async () => {
    const poll = vi.fn().mockResolvedValue(makeJob("running"));
    const wait = vi.fn().mockResolvedValue(undefined);

    const result = await pollDurableJob(makeJob("queued"), {
      poll,
      wait,
      onUpdate: vi.fn(),
      maxPolls: 3,
    });

    expect(result.state).toBe("running");
    expect(poll).toHaveBeenCalledTimes(3);
  });

  it("applies the capped backoff with a simulated clock", async () => {
    vi.useFakeTimers();
    const poll = vi
      .fn()
      .mockResolvedValueOnce(makeJob("running"))
      .mockResolvedValueOnce(makeJob("running"))
      .mockResolvedValueOnce(makeJob("succeeded"));
    const polling = pollDurableJob(makeJob("queued"), { poll, onUpdate: vi.fn() });

    await vi.advanceTimersByTimeAsync(999);
    expect(poll).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_499);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2_500);

    await expect(polling).resolves.toMatchObject({ state: "succeeded" });
    expect(poll).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it("resumes after network loss without manufacturing a failed job", async () => {
    const poll = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce(makeJob("running"))
      .mockResolvedValueOnce(makeJob("succeeded"));
    const onUpdate = vi.fn();
    const onNetworkError = vi.fn();
    const onConnectionRestored = vi.fn();

    const result = await pollDurableJob(makeJob("queued"), {
      poll,
      wait: vi.fn().mockResolvedValue(undefined),
      onUpdate,
      onNetworkError,
      onConnectionRestored,
    });

    expect(result.state).toBe("succeeded");
    expect(onNetworkError).toHaveBeenCalledTimes(1);
    expect(onConnectionRestored).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls.map(([updated]) => updated.state)).toEqual(["running", "succeeded"]);
  });

  it("aborts browser polling on close without mutating the server job", () => {
    const controllers = [new AbortController(), new AbortController()];
    const serverJob = makeJob("running");

    abortClientPolling(controllers);

    expect(controllers.every((controller) => controller.signal.aborted)).toBe(true);
    expect(serverJob.state).toBe("running");
  });
});
