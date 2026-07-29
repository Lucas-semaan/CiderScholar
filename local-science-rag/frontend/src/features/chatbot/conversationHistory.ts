import type { ChatConversationSummary, DurableJob } from "@/types/api";

import { isTerminalJob } from "./jobPolling";

export interface ConversationGroup {
  label: string;
  conversations: ChatConversationSummary[];
}

function parseDatabaseDate(value: string): Date {
  const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  return new Date(normalized);
}

export function createConversationTitle(question: string): string {
  const cleaned = question.replace(/\s+/g, " ").trim();
  if (cleaned.length <= 56) return cleaned;
  return `${cleaned.slice(0, 53).trimEnd()}…`;
}

export function withTrackedJobCounts(
  conversations: ChatConversationSummary[],
  jobs: readonly DurableJob[],
): ChatConversationSummary[] {
  const knownConversationIds = new Set(jobs.map((job) => job.conversation_id));
  return conversations.map((conversation) =>
    knownConversationIds.has(conversation.id)
      ? {
          ...conversation,
          active_job_count: jobs.filter(
            (job) => job.conversation_id === conversation.id && !isTerminalJob(job),
          ).length,
        }
      : conversation,
  );
}

export function activeJobBadgeLabel(count: number): string {
  return `${count} ${count > 1 ? "travaux" : "travail"} en cours`;
}

export function groupConversations(
  conversations: ChatConversationSummary[],
  now = new Date(),
): ConversationGroup[] {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const buckets: ConversationGroup[] = [
    { label: "Aujourd’hui", conversations: [] },
    { label: "Hier", conversations: [] },
    { label: "7 derniers jours", conversations: [] },
    { label: "30 derniers jours", conversations: [] },
    { label: "Plus ancien", conversations: [] },
  ];

  for (const conversation of conversations) {
    const updated = parseDatabaseDate(conversation.updated_at);
    const updatedDay = new Date(updated.getFullYear(), updated.getMonth(), updated.getDate());
    const elapsedDays = Math.floor((today.getTime() - updatedDay.getTime()) / 86_400_000);
    const bucketIndex =
      elapsedDays <= 0 ? 0 : elapsedDays === 1 ? 1 : elapsedDays < 7 ? 2 : elapsedDays < 30 ? 3 : 4;
    buckets[bucketIndex]?.conversations.push(conversation);
  }

  return buckets.filter((bucket) => bucket.conversations.length > 0);
}
