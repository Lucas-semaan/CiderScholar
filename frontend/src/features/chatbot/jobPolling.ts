import type { DurableJob, JobState } from "@/types/api";

const terminalStates: ReadonlySet<JobState> = new Set(["succeeded", "failed", "cancelled"]);

export const pollingBackoffMilliseconds = [1_000, 1_500, 2_500, 4_000, 5_000] as const;

export function isTerminalJob(job: DurableJob): boolean {
  return terminalStates.has(job.state);
}

export function pollingDelay(pollIndex: number): number {
  const boundedIndex = Math.min(Math.max(0, pollIndex), pollingBackoffMilliseconds.length - 1);
  return pollingBackoffMilliseconds[boundedIndex] ?? pollingBackoffMilliseconds[0];
}

export function abortClientPolling(controllers: Iterable<AbortController>): void {
  for (const controller of controllers) controller.abort();
}

function waitForDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const timeoutId = globalThis.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        globalThis.clearTimeout(timeoutId);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

interface PollDurableJobOptions {
  poll: (jobId: string, signal?: AbortSignal) => Promise<DurableJob>;
  onUpdate: (job: DurableJob) => void;
  onNetworkError?: (error: TypeError) => void;
  onConnectionRestored?: () => void;
  signal?: AbortSignal;
  maxPolls?: number;
  wait?: (milliseconds: number, signal?: AbortSignal) => Promise<void>;
}

export async function pollDurableJob(
  initialJob: DurableJob,
  {
    poll,
    onUpdate,
    onNetworkError,
    onConnectionRestored,
    signal,
    maxPolls = 720,
    wait = waitForDelay,
  }: PollDurableJobOptions,
): Promise<DurableJob> {
  let currentJob = initialJob;
  let networkUnavailable = false;
  for (let pollIndex = 0; pollIndex < maxPolls && !isTerminalJob(currentJob); pollIndex += 1) {
    await wait(pollingDelay(pollIndex), signal);
    if (signal?.aborted) throw signal.reason;
    try {
      currentJob = await poll(currentJob.id, signal);
    } catch (error: unknown) {
      if (signal?.aborted) throw signal.reason;
      if (error instanceof TypeError) {
        networkUnavailable = true;
        onNetworkError?.(error);
        continue;
      }
      throw error;
    }
    if (networkUnavailable) {
      networkUnavailable = false;
      onConnectionRestored?.();
    }
    onUpdate(currentJob);
  }
  return currentJob;
}
