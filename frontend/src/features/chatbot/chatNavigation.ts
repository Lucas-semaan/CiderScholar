import type { DurableJob } from "@/types/api";

interface NavigationState {
  enqueueing: boolean;
  conversationLoading: boolean;
  trackedJobs: readonly DurableJob[];
}

export function conversationNavigationDisabled({
  enqueueing,
  conversationLoading,
}: NavigationState): boolean {
  return enqueueing || conversationLoading;
}

export function jobsForConversation(
  jobs: readonly DurableJob[],
  conversationId: string | null,
): DurableJob[] {
  return jobs.filter((job) => job.conversation_id === conversationId);
}

export function terminalJobDisposition(
  job: DurableJob,
  activeConversationId: string | null,
): "reload_active" | "notify_other" {
  return job.conversation_id === activeConversationId ? "reload_active" : "notify_other";
}
