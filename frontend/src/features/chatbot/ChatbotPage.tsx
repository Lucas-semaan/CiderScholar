import { useEffect, useRef, useState } from "react";

import { Bot, History } from "lucide-react";

import { Card } from "@/components/ui/Card";
import { api } from "@/lib/api";
import type { ChatConversationSummary, DurableJob } from "@/types/api";

import { ChatMessage } from "./ChatMessage";
import { ChatbotHero, ConversationHeader } from "./ChatbotChrome";
import { ChatComposer } from "./ChatComposer";
import {
  conversationNavigationDisabled,
  jobsForConversation,
  terminalJobDisposition,
} from "./chatNavigation";
import type { ChatMessage as ChatMessageValue } from "./chatSession";
import { ConversationSidebar } from "./ConversationSidebar";
import { createConversationTitle, withTrackedJobCounts } from "./conversationHistory";
import { toConversationMessages, toSummary, welcomeMessage } from "./conversationView";
import {
  appendPersistedUserMessage,
  acquireSubmissionLock,
  createPendingChatSubmission,
  enqueuePendingChat,
  loadConversationWithActiveJobs,
  reloadTerminalConversation,
  releaseSubmissionLock,
  type ChatInteractionMode,
  type PendingChatSubmission,
} from "./durableChat";
import { JobStatusCard } from "./JobStatusCard";
import { JobCompletionNotice } from "./JobCompletionNotice";
import { useDurableJobs } from "./useDurableJobs";

export function ChatbotPage() {
  const [messages, setMessages] = useState<ChatMessageValue[]>([welcomeMessage]);
  const [conversations, setConversations] = useState<ChatConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [externalSources, setExternalSources] = useState(false);
  const [figureAnalysis, setFigureAnalysis] = useState(false);
  const [figureAnalysisAvailable, setFigureAnalysisAvailable] = useState(false);
  const [figureAnalysisEstimate, setFigureAnalysisEstimate] = useState("+12 à 18 min");
  const [deepResearch, setDeepResearch] = useState(false);
  const [interactionMode, setInteractionMode] = useState<ChatInteractionMode>("auto");
  const [deepResearchAvailable, setDeepResearchAvailable] = useState(false);
  const [administrator, setAdministrator] = useState(false);
  const [enqueueing, setEnqueueing] = useState(false);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [completionNotice, setCompletionNotice] = useState<DurableJob | null>(null);
  const pendingSubmissionRef = useRef<PendingChatSubmission | null>(null);
  const submissionLockRef = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);
  const handledTerminalsRef = useRef(new Set<string>());
  const { jobs, removeJob, trackJob, trackJobs } = useDurableJobs({
    onTerminal: (job) => {
      if (terminalJobDisposition(job, activeConversationId) === "notify_other") {
        setCompletionNotice(job);
      }
    },
  });

  const activeConversation = conversations.find(
    (conversation) => conversation.id === activeConversationId,
  );
  const interactionDisabled = conversationNavigationDisabled({
    enqueueing,
    conversationLoading,
    trackedJobs: jobs,
  });
  const activeJobs = jobsForConversation(jobs, activeConversationId);
  const sidebarConversations = withTrackedJobCounts(conversations, jobs);
  const conversationContextAvailable = messages.some(
    (message) => message.role === "assistant" && Boolean(message.response?.sources?.length),
  );

  useEffect(() => {
    let cancelled = false;
    void api.system.settings().then(
      (settings) => {
        if (!cancelled) {
          setAdministrator(settings.administrator);
          setDeepResearchAvailable(settings.deep_research.available);
          if (!settings.deep_research.available) setDeepResearch(false);
          setFigureAnalysisAvailable(settings.figure_analysis.available);
          setFigureAnalysisEstimate(
            `+${Math.ceil(settings.figure_analysis.estimated_min_seconds / 60)} à ${Math.ceil(
              settings.figure_analysis.estimated_max_seconds / 60,
            )} min`,
          );
          if (!settings.figure_analysis.available) setFigureAnalysis(false);
        }
      },
      () => {
        if (!cancelled) setAdministrator(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const initializeHistory = async () => {
      setHistoryLoading(true);
      try {
        const response = await api.chatbot.conversations();
        if (cancelled) return;
        setConversations(response.conversations);
        const latest = response.conversations[0];
        if (latest) {
          setConversationLoading(true);
          const conversation = await loadConversationWithActiveJobs(latest.id, trackJobs);
          if (cancelled) return;
          setActiveConversationId(conversation.id);
          setMessages([welcomeMessage, ...toConversationMessages(conversation)]);
        }
      } catch (caught: unknown) {
        if (!cancelled) {
          setHistoryError(
            caught instanceof Error ? caught.message : "L’historique n’a pas pu être chargé.",
          );
        }
      } finally {
        if (!cancelled) {
          setConversationLoading(false);
          setHistoryLoading(false);
        }
      }
    };

    void initializeHistory();
    return () => {
      cancelled = true;
    };
  }, [trackJobs]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, enqueueing]);

  useEffect(() => {
    const terminalJob = jobs.find(
      (job) =>
        (job.state === "succeeded" || job.state === "failed" || job.state === "cancelled") &&
        job.conversation_id === activeConversationId &&
        !handledTerminalsRef.current.has(job.id),
    );
    if (!terminalJob) return;

    handledTerminalsRef.current.add(terminalJob.id);
    void reloadTerminalConversation(terminalJob)
      .then((conversation) => {
        setMessages([welcomeMessage, ...toConversationMessages(conversation)]);
        setConversations((previous) => [
          toSummary(conversation),
          ...previous.filter((item) => item.id !== conversation.id),
        ]);
        if (terminalJob.state !== "failed") removeJob(terminalJob.id);
      })
      .catch((caught: unknown) => {
        handledTerminalsRef.current.delete(terminalJob.id);
        setError(caught instanceof Error ? caught.message : "La réponse n’a pas pu être relue.");
      });
  }, [activeConversationId, jobs, removeJob]);

  const refreshHistory = async () => {
    const response = await api.chatbot.conversations();
    setConversations(response.conversations);
  };

  const newConversation = () => {
    setActiveConversationId(null);
    setMessages([welcomeMessage]);
    setDraft("");
    setFigureAnalysis(false);
    setInteractionMode("auto");
    setError(null);
    setHistoryOpen(false);
  };

  const selectConversation = async (conversationId: string) => {
    if (interactionDisabled || conversationId === activeConversationId) {
      setHistoryOpen(false);
      return;
    }
    setConversationLoading(true);
    setError(null);
    try {
      const conversation = await loadConversationWithActiveJobs(conversationId, trackJobs);
      setActiveConversationId(conversation.id);
      setMessages([welcomeMessage, ...toConversationMessages(conversation)]);
      setDraft("");
      setFigureAnalysis(false);
      setInteractionMode("auto");
      setHistoryOpen(false);
    } catch (caught: unknown) {
      setHistoryError(
        caught instanceof Error ? caught.message : "Cette conversation n’a pas pu être chargée.",
      );
    } finally {
      setConversationLoading(false);
    }
  };

  const renameConversation = async (conversationId: string, title: string) => {
    try {
      const updated = await api.chatbot.renameConversation(conversationId, title);
      setConversations((previous) =>
        previous.map((conversation) =>
          conversation.id === conversationId ? toSummary(updated) : conversation,
        ),
      );
      setHistoryError(null);
    } catch (caught: unknown) {
      setHistoryError(
        caught instanceof Error ? caught.message : "Le titre n’a pas pu être modifié.",
      );
      throw caught;
    }
  };

  const deleteConversation = async (conversationId: string) => {
    try {
      await api.chatbot.deleteConversation(conversationId);
      setConversations((previous) =>
        previous.filter((conversation) => conversation.id !== conversationId),
      );
      if (conversationId === activeConversationId) newConversation();
      setHistoryError(null);
    } catch (caught: unknown) {
      setHistoryError(
        caught instanceof Error ? caught.message : "La conversation n’a pas pu être supprimée.",
      );
      throw caught;
    }
  };

  const favoriteConversation = async (conversationId: string, favorite: boolean) => {
    await api.chatbot.favoriteConversation(conversationId, favorite);
    setConversations((previous) =>
      previous.map((conversation) =>
        conversation.id === conversationId ? { ...conversation, favorite } : conversation,
      ),
    );
  };

  const saveFeedback = async (messageId: string, helpful: boolean) => {
    await api.chatbot.feedback(messageId, helpful);
    setMessages((previous) =>
      previous.map((message) => (message.id === messageId ? { ...message, helpful } : message)),
    );
  };

  const exportActiveConversation = async (format: "markdown" | "pdf") => {
    if (!activeConversationId) return;
    const blob = await api.chatbot.export([activeConversationId], [], format);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `ciderscholar-conversation.${format === "markdown" ? "md" : "pdf"}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const retryJob = async (jobId: string) => {
    try {
      const retried = await api.jobs.retry(jobId, crypto.randomUUID());
      removeJob(jobId);
      trackJob(retried);
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Le travail n’a pas pu être relancé.");
      throw caught;
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      const cancelled = await api.jobs.cancel(jobId);
      removeJob(jobId);
      trackJob(cancelled);
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Le travail n’a pas pu être annulé.");
      throw caught;
    }
  };

  const ask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = draft.trim();
    if (question.length < 2 || interactionDisabled || !acquireSubmissionLock(submissionLockRef))
      return;

    const existingPending = pendingSubmissionRef.current;
    const pending =
      existingPending?.message === question &&
      existingPending.useExternalSources === externalSources &&
      existingPending.analyzeFigures === figureAnalysis &&
      existingPending.mode === (deepResearch ? "deep_research" : "quick") &&
      existingPending.interactionMode === interactionMode
        ? existingPending
        : createPendingChatSubmission(
            question,
            externalSources,
            undefined,
            deepResearch,
            interactionMode,
            figureAnalysis,
          );
    pendingSubmissionRef.current = pending;
    setDraft("");
    setEnqueueing(true);
    setError(null);
    try {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const created = await api.chatbot.createConversation(createConversationTitle(question));
        conversationId = created.id;
        setActiveConversationId(created.id);
        setConversations((previous) => [toSummary(created), ...previous]);
      }

      const accepted = await enqueuePendingChat(conversationId, pending);
      pendingSubmissionRef.current = null;
      setMessages((previous) => appendPersistedUserMessage(previous, accepted.user_message));
      trackJob(accepted.job);
      setConversations((previous) =>
        previous.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                active_job_count: conversation.active_job_count + 1,
                message_count: conversation.message_count + 1,
                last_message: accepted.user_message.content,
              }
            : conversation,
        ),
      );
      try {
        await refreshHistory();
        setHistoryError(null);
      } catch (caught: unknown) {
        setHistoryError(
          caught instanceof Error ? caught.message : "L’historique n’a pas pu être actualisé.",
        );
      }
    } catch (caught: unknown) {
      setDraft(question);
      setError(
        caught instanceof Error ? caught.message : "La demande n’a pas pu être enregistrée.",
      );
      try {
        await refreshHistory();
      } catch {
        // The primary response error remains the most useful feedback here.
      }
    } finally {
      releaseSubmissionLock(submissionLockRef);
      setEnqueueing(false);
    }
  };

  return (
    <div className="-mx-4 -my-7 flex min-h-[calc(100vh-64px)] overflow-hidden sm:-mx-7 lg:-mx-10 lg:-my-10">
      <ConversationSidebar
        activeConversationId={activeConversationId}
        conversations={sidebarConversations}
        disabled={interactionDisabled}
        loading={historyLoading}
        onClose={() => setHistoryOpen(false)}
        onDelete={deleteConversation}
        onFavorite={favoriteConversation}
        onNew={newConversation}
        onRename={renameConversation}
        onSelect={(conversationId) => void selectConversation(conversationId)}
        open={historyOpen}
      />
      {completionNotice && (
        <JobCompletionNotice
          conversationTitle={
            conversations.find((item) => item.id === completionNotice.conversation_id)?.title ??
            "Conversation scientifique"
          }
          job={completionNotice}
          onDismiss={() => setCompletionNotice(null)}
          onOpen={() => {
            void selectConversation(completionNotice.conversation_id);
            setCompletionNotice(null);
          }}
        />
      )}

      <div className="min-w-0 flex-1 overflow-y-auto bg-stone-50">
        <div className="mx-auto w-full max-w-[1180px] space-y-5 p-4 sm:p-7 lg:p-8">
          <ChatbotHero />

          {historyError && (
            <div
              className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
              role="alert"
            >
              {historyError}
            </div>
          )}

          <Card className="overflow-hidden">
            <ConversationHeader
              active={Boolean(activeConversationId)}
              disabled={interactionDisabled}
              onExport={(format) => void exportActiveConversation(format)}
              onHistory={() => setHistoryOpen(true)}
              onNew={newConversation}
              title={activeConversation?.title ?? "Nouvelle conversation scientifique"}
            />

            <div
              aria-live="polite"
              className="max-h-[58vh] min-h-[430px] space-y-5 overflow-y-auto bg-stone-50/60 p-4 sm:p-6"
              role="log"
            >
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  onFeedback={(messageId, helpful) => void saveFeedback(messageId, helpful)}
                />
              ))}
              {activeJobs.map((job) => (
                <JobStatusCard
                  job={job}
                  key={job.id}
                  onCancel={(cancellableJob) => cancelJob(cancellableJob.id)}
                  onRetry={(failedJob) => retryJob(failedJob.id)}
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

            <ChatComposer
              allowDeepResearch={deepResearchAvailable && interactionMode !== "conversation"}
              allowExternalSources={
                administrator && !deepResearch && interactionMode !== "conversation"
              }
              conversationContextAvailable={conversationContextAvailable}
              deepResearch={deepResearch}
              disabled={interactionDisabled}
              draft={draft}
              error={error}
              externalSources={externalSources}
              figureAnalysis={figureAnalysis}
              figureAnalysisAvailable={figureAnalysisAvailable}
              figureAnalysisEstimate={figureAnalysisEstimate}
              interactionMode={interactionMode}
              onDraftChange={setDraft}
              onDeepResearchChange={(enabled) => {
                setDeepResearch(enabled);
                if (enabled) {
                  setExternalSources(false);
                  setInteractionMode("research");
                }
              }}
              onExternalSourcesChange={setExternalSources}
              onFigureAnalysisChange={(enabled) => {
                setFigureAnalysis(enabled);
                if (enabled) setInteractionMode("research");
              }}
              onInteractionModeChange={(mode) => {
                setInteractionMode(mode);
                if (mode === "conversation") {
                  setDeepResearch(false);
                  setExternalSources(false);
                  setFigureAnalysis(false);
                }
              }}
              onSubmit={(event) => void ask(event)}
              showSuggestions={messages.length === 1}
            />
          </Card>
        </div>
      </div>
    </div>
  );
}
