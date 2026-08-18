import type { JobState, JobStep, JobType } from "./chat";

export type * from "./chat";

export interface RuntimeSettings {
  offline_mode: boolean;
  bibliographic_apis: boolean;
  llm_provider: "argo";
  llm_model: string;
  llm_key_configured: boolean;
  embedding_model: string;
  embedding_device: string;
  embedding_batch_size: number;
  passages_per_article: number;
  database_name: string;
  data_directory: string;
  administrator: boolean;
  memory: {
    detected_total_gb: number | null;
    recommended_profile: "8gb" | "16gb" | null;
    active_profile: "custom" | "8gb" | "16gb";
    applied_automatically: false;
  };
  deep_research: {
    available: boolean;
    state:
      | "disabled"
      | "missing_evaluation"
      | "invalid_evaluation"
      | "profile_mismatch"
      | "corpus_mismatch"
      | "ready";
    message: string;
    bundle_sha256: string | null;
  };
  figure_analysis: {
    available: boolean;
    model: string;
    max_figures: number;
    estimated_min_seconds: number;
    estimated_max_seconds: number;
  };
  corpus_update: {
    latest_state: "disabled" | "sync_unavailable" | "latest_unavailable" | "invalid" | "available";
    installed_version: string | null;
    available_version: string | null;
    update_available: boolean;
    download_required: boolean;
    message: string;
    published_at: string | null;
  };
  application_update: {
    state:
      "disabled" | "unavailable" | "invalid" | "current" | "available" | "deferred_active_jobs";
    installed_version: string;
    available_version: string | null;
    installer_path: string | null;
    active_jobs: number;
    message: string;
  };
  retrieval: {
    lexical_weight: number;
    vector_weight: number;
    reranker_weight: number;
    default_article_count: number;
  };
  harvest: {
    enabled: boolean;
    cadence_hours: number;
    per_source_limit: number;
    free_openalex_only: boolean;
  };
  publisher_access: PublisherAccessStatus;
}

export interface ReadinessCheck {
  state: "ready" | "blocked";
  message: string;
  action: string;
}

export interface ReadinessReport {
  schema_version: 1;
  ready: boolean;
  checked_at: string;
  checks: {
    argo: ReadinessCheck;
    worker: ReadinessCheck;
    corpus: ReadinessCheck;
    disk: ReadinessCheck;
  };
  queue: {
    depth: number;
    queued: number;
    running: number;
    cancel_requested: number;
    oldest_created_at: string | null;
    oldest_age_seconds: number | null;
  };
}

export type DiagnosticWorkerState = "healthy" | "stale";

export interface DiagnosticActiveJob {
  id: string;
  type: JobType;
  state: JobState;
  step: JobStep;
  created_at: string;
  heartbeat_at: string | null;
}

export interface DiagnosticWarning {
  code: string;
  severity: "info" | "warning";
  message: string;
}

export interface SystemDiagnostics {
  checked_at: string;
  active_jobs: DiagnosticActiveJob[];
  worker: {
    state: DiagnosticWorkerState;
    heartbeat_at: string | null;
    heartbeat_age_seconds: number | null;
  };
  process: {
    api_rss_bytes: number | null;
    worker_rss_bytes: number | null;
    system_available_bytes: number | null;
  };
  warnings: DiagnosticWarning[];
}

export interface MaintenanceSchedule {
  administrator: true;
  due: boolean;
  prompt: boolean;
  next_due_at: string | null;
  last_success: {
    schema_version: 1;
    completed_at: string;
    corpus_version: string;
    result: "published";
    job_id: string;
  } | null;
  last_deferred_at: string | null;
}

export interface OnboardingStatus {
  schema_version: 1;
  model_ready: boolean;
  sharepoint_ready: boolean;
  corpus_ready: boolean;
  argo_ready: boolean;
  memory_ready: boolean;
  completed: boolean;
  synchronized_root: string | null;
  installed_corpus_version: string | null;
  memory: {
    detected_total_gb: number | null;
    recommended_profile: "8gb" | "16gb" | null;
    active_profile: "custom" | "8gb" | "16gb";
    applied_automatically: false;
  };
}

export interface ArgoKeyStatus {
  configured: boolean;
}

export interface ArgoConnectionStatus {
  state: "ready" | "missing" | "rejected" | "network_unavailable" | "model_unavailable";
  configured: boolean;
  message: string;
}

export interface PublisherAccessStatus {
  enabled: boolean;
  credentials_configured: boolean;
  profiles: Array<{ id: string; label: string }>;
  max_records_per_run: number;
}

export interface PublisherAccessRun {
  id: string;
  profile_id: string;
  authorization_reference: string;
  state: "queued" | "running" | "completed" | "partial" | "failed";
  requested_record_count: number;
  completed_record_count: number;
  failed_record_count: number;
  error_type: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  items: Array<Record<string, unknown>>;
}

export interface Overview {
  corpus: CorpusStatistics;
  bibliography: LibraryStatistics;
  activity: {
    queries: number;
  };
  runtime: RuntimeSettings;
}

export interface CorpusStatistics {
  articles: number;
  chunks: number;
  indexed_chunks: number;
  index_coverage: number;
  failed_ingestions: number;
  ocr_required: number;
}

export interface CorpusArticle {
  id: string;
  title: string;
  doi: string | null;
  journal: string | null;
  work_type: string | null;
  publisher: string | null;
  publication_year: number | null;
  language: string | null;
  validation_status: string;
  pdf_path: string;
  source: string;
  created_at: string;
  indexed_at: string | null;
  chunk_count: number;
  indexed_chunk_count: number | null;
}

export interface IngestionJob {
  id: number;
  pdf_path: string;
  sha256: string | null;
  state: string;
  article_id: string | null;
  error_type: string | null;
  error_message: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
}

export interface CorpusResponse {
  articles: CorpusArticle[];
  jobs: IngestionJob[];
  summary: {
    articles: number;
    chunks: number;
    indexed_chunks: number;
    failed_jobs: number;
    ocr_jobs: number;
  };
}

export interface IngestionReport {
  pdf_path: string;
  sha256: string | null;
  article_id: string | null;
  status: "chunks_ready" | "duplicate" | "ocr_required" | "failed";
  duplicate_reason: "sha256" | "doi" | "normalized_text" | null;
  page_count: number;
  chunk_count: number;
  resumed_from_cache: boolean;
  error_type: string | null;
  error_message: string | null;
  duration_seconds: number;
}

export interface LibraryStatistics {
  documents: number;
  full_texts: number;
  abstract_only: number;
}

export interface LibrarySummary {
  statistics: LibraryStatistics;
  filters: { themes: string[]; sources: string[] };
}

export interface LibraryRecord {
  id: string;
  library_id: string;
  canonical_key: string;
  doi: string | null;
  title: string;
  abstract: string | null;
  authors: string;
  journal: string | null;
  work_type: string | null;
  publisher: string | null;
  publication_year: number | null;
  citation_count: number | null;
  url: string | null;
  embedding_status: string;
  relevance_status: "unreviewed" | "accepted" | "review" | "rejected";
  relevance_score: number | null;
  relevance_reason: string | null;
  relevance_theme: string | null;
  themes: string[];
  sources: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  document_type: "full_text" | "abstract_only";
  article_id: string | null;
  pdf_available: boolean;
  pdf_path: string | null;
  validation_status: string | null;
  chunk_count: number;
  indexed_chunk_count: number;
}

export interface LibraryRecordsResponse {
  total: number;
  records: LibraryRecord[];
  limit: number;
  offset: number;
}

export interface LibraryReviewDecisionResponse {
  record_id: string;
  title: string;
  decision: "accepted" | "rejected";
  deleted: boolean;
  vectors_deleted: number;
}

export interface SynthesisQuery {
  id: string;
  original_query: string;
  created_at: string;
  duration_seconds: number | null;
  selected_article_ids: string[];
  model_version: string | null;
  evidence_completed: number;
  evidence_failed: number;
  evidence_total: number;
  synthesis_state: string | null;
  synthesis_updated_at: string | null;
}

export interface SynthesisDetail {
  summary: SynthesisQuery;
  evidence_runs: Array<Record<string, unknown>>;
  theme_plan: Record<string, unknown> | null;
  themes: Array<Record<string, unknown>>;
  result: {
    answer_markdown: string;
    bibliography: Array<Record<string, unknown>>;
    [key: string]: unknown;
  } | null;
}
