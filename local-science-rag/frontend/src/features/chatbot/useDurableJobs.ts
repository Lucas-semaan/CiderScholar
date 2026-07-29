import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { DurableJob } from "@/types/api";

import { abortClientPolling, isTerminalJob, pollDurableJob } from "./jobPolling";

interface DurableJobTrackingOptions {
  onTerminal?: (job: DurableJob) => void;
  onPollingError?: (job: DurableJob, error: unknown) => void;
}

export function useDurableJobs(options: DurableJobTrackingOptions = {}) {
  const [jobs, setJobs] = useState<DurableJob[]>([]);
  const controllersRef = useRef(new Map<string, AbortController>());
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const updateJob = useCallback((updated: DurableJob) => {
    setJobs((previous) => [...previous.filter((job) => job.id !== updated.id), updated]);
  }, []);

  const trackJob = useCallback(
    (job: DurableJob) => {
      updateJob(job);
      if (isTerminalJob(job)) {
        optionsRef.current.onTerminal?.(job);
        return;
      }
      if (controllersRef.current.has(job.id)) return;

      const controller = new AbortController();
      controllersRef.current.set(job.id, controller);
      void pollDurableJob(job, {
        poll: api.jobs.poll,
        onUpdate: updateJob,
        signal: controller.signal,
      })
        .then((lastJob) => {
          if (isTerminalJob(lastJob)) optionsRef.current.onTerminal?.(lastJob);
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) optionsRef.current.onPollingError?.(job, error);
        })
        .finally(() => controllersRef.current.delete(job.id));
    },
    [updateJob],
  );

  const removeJob = useCallback((jobId: string) => {
    controllersRef.current.get(jobId)?.abort();
    controllersRef.current.delete(jobId);
    setJobs((previous) => previous.filter((job) => job.id !== jobId));
  }, []);

  const trackJobs = useCallback(
    (jobsToTrack: readonly DurableJob[]) => {
      jobsToTrack.forEach(trackJob);
    },
    [trackJob],
  );

  useEffect(
    () => () => {
      abortClientPolling(controllersRef.current.values());
      controllersRef.current.clear();
    },
    [],
  );

  return { jobs, removeJob, trackJob, trackJobs };
}
