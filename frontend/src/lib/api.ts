import type {
  ArgoConnectionStatus,
  ArgoKeyStatus,
  ChatConversation,
  ChatConversationSummary,
  ChatJobSubmitResponse,
  CorpusResponse,
  IngestionReport,
  DurableJob,
  LibraryRecordsResponse,
  LibraryReviewDecisionResponse,
  LibrarySummary,
  MaintenanceSchedule,
  OnboardingStatus,
  Overview,
  PublisherAccessRun,
  PublisherAccessStatus,
  ReadinessReport,
  RuntimeSettings,
  SystemDiagnostics,
  SynthesisDetail,
  SynthesisQuery,
  SuggestionReferenceSource,
  SuggestionSubmissionResult,
} from "@/types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchChecked(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(payload?.detail ?? `Erreur HTTP ${response.status}`, response.status);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await fetchChecked(path, init)).json() as Promise<T>;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

async function download(path: string, body: unknown): Promise<Blob> {
  return fetchChecked(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((response) => response.blob());
}

export interface LibraryRecordFilters {
  query: string;
  statuses: string[];
  theme: string;
  source: string;
  abstract: "all" | "with" | "without";
  availability: "all" | "full_text" | "abstract_only";
  limit: number;
  offset: number;
}

type CorpusUploadResponse = {
  reports?: IngestionReport[];
  job?: DurableJob;
  staged_files?: number;
};

type CorpusFolderResponse = {
  discovered_files: number;
  reports?: IngestionReport[];
  job?: DurableJob;
};

function createCorpusApi(rootPath: string) {
  return {
    list: () => request<CorpusResponse>(rootPath),
    upload: async (files: File[]) => {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      return request<CorpusUploadResponse>(`${rootPath}/upload`, { method: "POST", body });
    },
    folder: (folder: string, recursive: boolean) =>
      post<CorpusFolderResponse>(`${rootPath}/folder`, { folder, recursive }),
    index: (retryFailed: boolean) =>
      post<Record<string, unknown>>(`${rootPath}/index`, { retry_failed: retryFailed }),
    reindex: (articleId: string) =>
      post<Record<string, unknown>>(`${rootPath}/${articleId}/reindex`, {}),
    remove: (articleId: string) =>
      request<Record<string, number>>(`${rootPath}/${articleId}`, { method: "DELETE" }),
  };
}

export const api = {
  argoKey: {
    status: () => request<ArgoKeyStatus>("/api/argo-key"),
    save: (key: string) =>
      request<ArgoKeyStatus>("/api/argo-key", {
        method: "PUT",
        body: JSON.stringify({ key }),
      }),
    remove: () => request<ArgoKeyStatus>("/api/argo-key", { method: "DELETE" }),
    test: () => post<ArgoConnectionStatus>("/api/argo-key/test", {}),
  },
  chatbot: {
    conversations: () =>
      request<{ conversations: ChatConversationSummary[] }>("/api/chatbot/conversations"),
    searchConversations: (query: string) =>
      request<{ conversations: ChatConversationSummary[] }>(
        `/api/chatbot/conversations/search?query=${encodeURIComponent(query)}`,
      ),
    conversation: (conversationId: string) =>
      request<ChatConversation>(`/api/chatbot/conversations/${conversationId}`),
    createConversation: (title: string) =>
      post<ChatConversation>("/api/chatbot/conversations", { title }),
    renameConversation: (conversationId: string, title: string) =>
      request<ChatConversation>(`/api/chatbot/conversations/${conversationId}`, {
        method: "PUT",
        body: JSON.stringify({ title }),
      }),
    deleteConversation: (conversationId: string) =>
      request<{ deleted: boolean }>(`/api/chatbot/conversations/${conversationId}`, {
        method: "DELETE",
      }),
    favoriteConversation: (conversationId: string, favorite: boolean) =>
      request<{ favorite: boolean }>(
        `/api/chatbot/conversations/${encodeURIComponent(conversationId)}/favorite`,
        {
          method: "PUT",
          body: JSON.stringify({ favorite }),
        },
      ),
    feedback: (messageId: string, helpful: boolean) =>
      request<{ helpful: boolean }>(
        `/api/chatbot/messages/${encodeURIComponent(messageId)}/feedback`,
        {
          method: "PUT",
          body: JSON.stringify({ helpful }),
        },
      ),
    export: (conversationIds: string[], messageIds: string[], format: "markdown" | "pdf") =>
      download("/api/chatbot/exports", {
        conversation_ids: conversationIds,
        message_ids: messageIds,
        format,
      }),
  },
  jobs: {
    enqueueEvaluation: (payload: {
      message: string;
      client_request_id: string;
      run_id: string;
      question_id: string;
      profile: "p0" | "p1" | "p2";
    }) => post<ChatJobSubmitResponse>("/api/chatbot/evaluation/jobs", payload),
    enqueue: (
      conversationId: string,
      payload: {
        message: string;
        client_request_id: string;
        use_external_sources: boolean;
        analyze_figures: boolean;
        mode: "quick" | "deep_research";
        interaction_mode: "auto" | "research" | "conversation";
        answer_effort: "concise" | "balanced" | "deep";
        evaluation_run_id?: string;
        evaluation_question_id?: string;
        evaluation_profile?: "p0" | "p1" | "p2";
      },
    ) =>
      post<ChatJobSubmitResponse>(
        `/api/chatbot/conversations/${encodeURIComponent(conversationId)}/jobs`,
        payload,
      ),
    poll: (jobId: string, signal?: AbortSignal) =>
      request<DurableJob>(
        `/api/jobs/${encodeURIComponent(jobId)}`,
        signal ? { signal } : undefined,
      ),
    cancel: (jobId: string) =>
      post<DurableJob>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {}),
    retry: (jobId: string, clientRequestId: string) =>
      post<DurableJob>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
        client_request_id: clientRequestId,
      }),
  },
  system: {
    overview: () => request<Overview>("/api/system/overview"),
    settings: () => request<RuntimeSettings>("/api/system/settings"),
    diagnostics: () => request<SystemDiagnostics>("/api/system/diagnostics"),
    updateSettings: (payload: {
      default_article_count: number;
      lexical_weight: number;
      vector_weight: number;
      reranker_weight: number;
      embedding_batch_size: number;
      passages_per_article: number;
    }) =>
      request<RuntimeSettings>("/api/system/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      }),
    llmHealth: () => request<Record<string, unknown>>("/health/llm"),
    shutdown: () =>
      post<{ state: string; message: string }>("/api/system/shutdown", { confirmed: true }),
  },
  diagnostics: {
    readiness: () => request<ReadinessReport>("/api/diagnostics/readiness"),
  },
  corpusUpdates: {
    download: () =>
      post<{ state: string; corpus_version: string; message: string }>(
        "/api/corpus-updates/download",
        { confirmed: true },
      ),
    installOnRestart: () =>
      post<{ state: string; corpus_version: string; message: string }>(
        "/api/corpus-updates/install-on-restart",
        { confirmed: true },
      ),
    rollbackOnRestart: () =>
      post<{ state: string; previous_path: string; message: string }>(
        "/api/corpus-updates/rollback-on-restart",
        { confirmed: true },
      ),
  },
  suggestions: {
    submitReference: (source: SuggestionReferenceSource, scientificComment: string) =>
      post<SuggestionSubmissionResult>("/api/suggestions", {
        source,
        scientific_comment: scientificComment || null,
      }),
    submitPdf: (
      file: File,
      scientificComment: string,
      confirmed: boolean,
      metadata: {
        title?: string | undefined;
        doi?: string | undefined;
        abstract?: string | undefined;
      },
    ) => {
      const body = new FormData();
      body.append("file", file);
      body.append("scientific_comment", scientificComment);
      body.append("transmit_pdf_confirmed", String(confirmed));
      if (metadata.title) body.append("title", metadata.title);
      if (metadata.doi) body.append("doi", metadata.doi);
      if (metadata.abstract) body.append("abstract", metadata.abstract);
      return request<SuggestionSubmissionResult>("/api/suggestions/pdf", {
        method: "POST",
        body,
      });
    },
  },
  adminMaintenance: {
    status: () => request<MaintenanceSchedule>("/api/admin/maintenance"),
    defer: () => post<MaintenanceSchedule>("/api/admin/maintenance/defer", { confirmed: true }),
    launch: () => post<DurableJob>("/api/admin/maintenance/launch", { confirmed: true }),
  },
  onboarding: {
    status: () => request<OnboardingStatus>("/api/onboarding"),
    chooseSharePoint: () =>
      post<{ path: string | null }>("/api/onboarding/choose-sharepoint", { confirmed: true }),
    configureSharePoint: (path: string, confirmUnexpectedName: boolean) =>
      request<OnboardingStatus>("/api/onboarding/sharepoint", {
        method: "PUT",
        body: JSON.stringify({ path, confirm_unexpected_name: confirmUnexpectedName }),
      }),
    installCorpus: () => post<OnboardingStatus>("/api/onboarding/corpus", { confirmed: true }),
    selectMemory: (profile: "8gb" | "16gb") =>
      request<OnboardingStatus>("/api/onboarding/memory", {
        method: "PUT",
        body: JSON.stringify({ profile }),
      }),
  },
  publisherAccess: {
    status: () => request<PublisherAccessStatus>("/api/publisher-access/status"),
    saveCredentials: (payload: {
      username: string;
      password: string;
      authorization_confirmed: true;
    }) =>
      request<{ credentials_configured: boolean }>("/api/publisher-access/credentials", {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    deleteCredentials: () =>
      request<{ credentials_configured: boolean }>("/api/publisher-access/credentials", {
        method: "DELETE",
      }),
    startRun: (payload: {
      profile_id: string;
      targets: string[];
      authorization_reference: string;
      authorization_confirmed: true;
    }) =>
      post<{ run_id: string; state: string; target_count: number }>(
        "/api/publisher-access/runs",
        payload,
      ),
    run: (runId: string) => request<PublisherAccessRun>(`/api/publisher-access/runs/${runId}`),
  },
  corpus: createCorpusApi("/api/corpus"),
  library: {
    summary: () => request<LibrarySummary>("/api/library/summary"),
    records: (filters: LibraryRecordFilters) => {
      const parameters = new URLSearchParams({
        query: filters.query,
        statuses: filters.statuses.join(","),
        abstract: filters.abstract,
        availability: filters.availability,
        limit: String(filters.limit),
        offset: String(filters.offset),
      });
      if (filters.theme) parameters.set("theme", filters.theme);
      if (filters.source) parameters.set("source", filters.source);
      return request<LibraryRecordsResponse>(`/api/library/records?${parameters}`);
    },
    decideReview: (recordId: string, decision: "accepted" | "rejected") =>
      post<LibraryReviewDecisionResponse>(
        `/api/library/records/${encodeURIComponent(recordId)}/decision`,
        { decision },
      ),
  },
  synthesis: {
    list: () => request<{ queries: SynthesisQuery[] }>("/api/synthesis"),
    detail: (queryId: string) => request<SynthesisDetail>(`/api/synthesis/${queryId}`),
    run: (queryId: string, resume = true) =>
      post<DurableJob>(`/api/synthesis/${queryId}/run`, { resume }),
  },
};
