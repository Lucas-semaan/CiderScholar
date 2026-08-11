import type { RefObject } from "react";

import { Bot, History } from "lucide-react";

import type { DurableJob } from "@/types/api";

import { ChatMessage } from "./ChatMessage";
import type { ChatMessage as ChatMessageValue } from "./chatSession";
import { JobStatusCard } from "./JobStatusCard";

interface ConversationTranscriptProps {
  activeJobs: readonly DurableJob[];
  conversationLoading: boolean;
  endRef: RefObject<HTMLDivElement | null>;
  enqueueing: boolean;
  messages: readonly ChatMessageValue[];
  onCancelJob: (jobId: string) => Promise<void>;
  onFeedback: (messageId: string, helpful: boolean) => Promise<void>;
  onRetryJob: (jobId: string) => Promise<void>;
}

export function ConversationTranscript({
  activeJobs,
  conversationLoading,
  endRef,
  enqueueing,
  messages,
  onCancelJob,
  onFeedback,
  onRetryJob,
}: ConversationTranscriptProps) {
  return (
    <div
      aria-live="polite"
      className="max-h-[58vh] min-h-[430px] space-y-5 overflow-y-auto bg-stone-50/60 p-4 sm:p-6"
      role="log"
    >
      {messages.map((message) => (
        <ChatMessage
          key={message.id}
          message={message}
          onFeedback={(messageId, helpful) => void onFeedback(messageId, helpful)}
        />
      ))}
      {activeJobs.map((job) => (
        <JobStatusCard
          job={job}
          key={job.id}
          onCancel={(cancellableJob) => onCancelJob(cancellableJob.id)}
          onRetry={(failedJob) => onRetryJob(failedJob.id)}
        />
      ))}
      {conversationLoading && (
        <div className="flex items-center gap-3 text-sm text-slate-500" role="status">
          <span className="grid size-9 place-items-center rounded-xl bg-slate-200">
            <History aria-hidden="true" className="size-4 animate-pulse" />
          </span>
          Chargement de la conversation…
        </div>
      )}
      {enqueueing && (
        <div className="flex items-center gap-3 text-sm text-slate-500">
          <span className="grid size-9 place-items-center rounded-xl bg-forest-600 text-white">
            <Bot aria-hidden="true" className="size-4" />
          </span>
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-soft">
            <p>Enregistrement de la demande…</p>
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
