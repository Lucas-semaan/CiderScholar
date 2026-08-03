import { api } from "@/lib/api";
import type {
  ChatConversation,
  ChatJobSubmitResponse,
  DurableJob,
  PersistedUserMessage,
} from "@/types/api";

import type { ChatMessage } from "./chatSession";

export type ChatInteractionMode = "auto" | "research" | "conversation";

export interface PendingChatSubmission {
  message: string;
  useExternalSources: boolean;
  analyzeFigures: boolean;
  mode: "quick" | "deep_research";
  interactionMode: ChatInteractionMode;
  clientRequestId: string;
}

export interface SubmissionLock {
  current: boolean;
}

export function acquireSubmissionLock(lock: SubmissionLock): boolean {
  if (lock.current) return false;
  lock.current = true;
  return true;
}

export function releaseSubmissionLock(lock: SubmissionLock): void {
  lock.current = false;
}

export function createPendingChatSubmission(
  message: string,
  useExternalSources: boolean,
  clientRequestId = crypto.randomUUID(),
  deepResearch = false,
  interactionMode: ChatInteractionMode = "auto",
  analyzeFigures = false,
): PendingChatSubmission {
  return {
    message,
    useExternalSources,
    analyzeFigures,
    mode: deepResearch ? "deep_research" : "quick",
    interactionMode: deepResearch ? "research" : interactionMode,
    clientRequestId,
  };
}

export function enqueuePendingChat(
  conversationId: string,
  submission: PendingChatSubmission,
  enqueue: typeof api.jobs.enqueue = api.jobs.enqueue,
): Promise<ChatJobSubmitResponse> {
  return enqueue(conversationId, {
    message: submission.message,
    client_request_id: submission.clientRequestId,
    use_external_sources: submission.useExternalSources,
    analyze_figures: submission.analyzeFigures,
    mode: submission.mode,
    interaction_mode: submission.interactionMode,
  });
}

export function appendPersistedUserMessage(
  messages: ChatMessage[],
  persisted: PersistedUserMessage,
): ChatMessage[] {
  if (messages.some((message) => message.id === persisted.id)) return messages;
  return [
    ...messages,
    {
      id: persisted.id,
      role: persisted.role,
      content: persisted.content,
    },
  ];
}

export function reloadSucceededConversation(
  job: DurableJob,
  load: (conversationId: string) => Promise<ChatConversation> = api.chatbot.conversation,
): Promise<ChatConversation> {
  if (job.state !== "succeeded") {
    return Promise.reject(new Error("Cannot reload a conversation before job success."));
  }
  return load(job.conversation_id);
}

export async function loadConversationWithActiveJobs(
  conversationId: string,
  trackJobs: (jobs: readonly DurableJob[]) => void,
  load: (id: string) => Promise<ChatConversation> = api.chatbot.conversation,
): Promise<ChatConversation> {
  const conversation = await load(conversationId);
  trackJobs(conversation.active_jobs);
  return conversation;
}
