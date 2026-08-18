export type JobType =
  "chat_answer" | "weekly_maintenance" | "deep_research" | "long_synthesis" | "corpus_ingestion";

export type JobState =
  "queued" | "running" | "succeeded" | "failed" | "cancel_requested" | "cancelled";

export type JobStep =
  | "waiting"
  | "planning"
  | "search"
  | "enrichment"
  | "reranking"
  | "evidence_selection"
  | "coverage"
  | "figure_analysis"
  | "generation"
  | "argo"
  | "validation"
  | "persistence"
  | "backup"
  // Compatibility with maintenance jobs persisted before the proposal workflow was retired.
  | "suggestions"
  | "harvest"
  | "index"
  | "publish"
  | "evidence"
  | "verification"
  | "synthesis"
  | "ingestion";

export type JobErrorCode = "timeout" | "quota" | "authentication" | "validation";

export interface JobError {
  code: JobErrorCode;
  message: string;
  retry_at: string | null;
}

export interface DurableJob {
  id: string;
  conversation_id: string;
  type: JobType;
  state: JobState;
  step: JobStep;
  attempt: number;
  available_at: string;
  created_at: string;
  updated_at: string;
  result_message_id: string | null;
  error: JobError | null;
}

export interface PersistedUserMessage {
  id: string;
  role: "user";
  content: string;
  created_at: string;
}

export interface ChatJobSubmitResponse {
  job: DurableJob;
  user_message: PersistedUserMessage;
}

export interface ChatbotSource {
  record_id: string;
  origin: "local_rag" | "external_api";
  evidence_level: "abstract" | "full_text";
  article_id: string | null;
  chunk_ids: number[];
  page_ranges: string[];
  figure_refs?: string[];
  title: string;
  authors: string[];
  doi: string | null;
  journal: string | null;
  publication_year: number | null;
  providers: string[];
  url: string | null;
  snippet: string;
}

export interface ChatbotFacetDraft {
  key: string;
  label: string;
  query: string;
  answer_markdown: string;
  cited_evidence_ids: string[];
  source_record_ids: string[];
}

export interface ChatbotEvaluationTrace {
  run_id: string;
  question_id: string;
  profile: "p0" | "p1" | "p2";
  question_sha256: string;
}

export type AnswerEffort = "concise" | "balanced" | "deep";

export interface ChatbotTiming {
  stage: string;
  duration_seconds: number;
  count: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  process_rss_before_gb?: number | null;
  process_rss_after_gb?: number | null;
  system_used_before_gb?: number | null;
  system_used_after_gb?: number | null;
  system_available_before_gb?: number | null;
  system_available_after_gb?: number | null;
}

export interface ChatbotRetrievalTrace {
  schema_version: 1;
  stage:
    | "abstract_search"
    | "full_text_search"
    | "full_text_axis_search"
    | "full_text_pool_merge"
    | "full_text_reranking"
    | "full_text_evidence_selection"
    | "supplemental_abstract_search"
    | "supplemental_full_text_search"
    | "supplemental_full_text_axis_search"
    | "supplemental_full_text_pool_merge"
    | "supplemental_full_text_reranking"
    | "supplemental_full_text_evidence_selection"
    | "evidence_merge"
    | "semantic_filter"
    | "llm_context";
  query_variant_count: number;
  lexical_candidate_count: number;
  dense_candidate_count: number;
  rrf_unique_candidate_count: number;
  fused_candidate_count: number;
  pre_rerank_candidate_count: number;
  post_rerank_candidate_count: number;
  selected_article_count: number;
  selected_passage_count: number;
  rejection_counts: Record<string, number>;
  vector_search_degraded: boolean;
}

export interface ScientificGenerationTrace {
  schema_version: 1;
  phase: "abstract" | "evidence" | "facet_draft" | "final_assembly" | "deterministic_abstention";
  outcome: "generated" | "partial_generated" | "abstained" | "failed";
  request_count: number;
  validation_retries: number;
  length_retries: number;
  correction_temperature: number | null;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface ChatbotResponse {
  message: string;
  retrieval_query: string;
  answer_markdown: string;
  sources: ChatbotSource[];
  warnings: string[];
  model: string;
  local_result_count: number;
  external_result_count: number;
  external_enrichment_used: boolean;
  prompt_tokens: number;
  completion_tokens: number;
  duration_seconds: number;
  generation_status?:
    "generated" | "partial_generated" | "abstained" | "extractive_fallback" | "diagnostic_only";
  diagnostic_code?: string | null;
  interaction_mode: "research" | "conversation";
  answer_effort?: AnswerEffort;
  timings?: ChatbotTiming[];
  retrieval_traces?: ChatbotRetrievalTrace[];
  generation_traces?: ScientificGenerationTrace[];
  reused_previous_sources: boolean;
  facet_drafts?: ChatbotFacetDraft[];
  figure_analysis_requested?: boolean;
  figure_analysis_count?: number;
  figure_analysis_duration_seconds?: number;
  figure_analysis_model?: string | null;
  evaluation?: ChatbotEvaluationTrace | null;
}

export interface ChatJobTerminalNotice {
  kind: "job_terminal_notice";
  job_id: string;
  state: "failed" | "cancelled";
  error_code: string | null;
  diagnostic_code: string | null;
}

export type StoredChatResponse = ChatbotResponse | ChatJobTerminalNotice;

export interface ChatConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message: string | null;
  active_job_count: number;
  favorite: boolean;
}

export interface StoredChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response: StoredChatResponse | null;
  response_time_milliseconds: number | null;
  created_at: string;
  helpful: boolean | null;
}

export interface ChatConversation extends ChatConversationSummary {
  messages: StoredChatMessage[];
  active_jobs: DurableJob[];
}
