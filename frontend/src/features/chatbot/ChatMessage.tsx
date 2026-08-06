import {
  Bot,
  ChartNoAxesCombined,
  Cloud,
  ExternalLink,
  LibraryBig,
  MessageCircleMore,
  ThumbsDown,
  ThumbsUp,
  Timer,
  UserRound,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/Badge";
import { cn, formatNumber } from "@/lib/cn";

import { formatResponseTime, type ChatMessage as ChatMessageValue } from "./chatSession";
import { sourceEvidenceLabel, sourceOriginLabel } from "./sourcePresentation";

export function ChatMessage({
  message,
  onFeedback,
}: {
  message: ChatMessageValue;
  onFeedback?: (messageId: string, helpful: boolean) => void;
}) {
  const assistant = message.role === "assistant";
  const terminalNotice = message.terminalNotice;
  const response = message.response;
  const facetDrafts = response?.facet_drafts ?? [];

  return (
    <article
      aria-label={assistant ? "Réponse de CiderScholar" : "Votre question"}
      className={cn("flex gap-3", assistant ? "items-start" : "items-start justify-end")}
    >
      {assistant && (
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-forest-600 text-white shadow-soft">
          <Bot aria-hidden="true" className="size-4" />
        </span>
      )}
      <div
        className={cn(
          "max-w-[min(860px,88%)] rounded-[18px] px-4 py-3 shadow-soft sm:px-5 sm:py-4",
          assistant
            ? "border border-slate-200 bg-white text-slate-700"
            : "bg-forest-700 text-white",
        )}
      >
        {assistant ? (
          <div className="space-y-3 text-sm leading-7 [&_a]:font-semibold [&_a]:text-forest-700 [&_h2]:text-base [&_h2]:font-bold [&_h3]:font-bold [&_li]:ml-5 [&_li]:list-disc [&_strong]:text-slate-900">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>
        )}

        {terminalNotice && (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
            <Badge tone={terminalNotice.state === "cancelled" ? "neutral" : "warning"}>
              {terminalNotice.state === "cancelled" ? "Traitement annulé" : "Réponse bloquée"}
            </Badge>
            {terminalNotice.diagnostic_code && <Badge>{terminalNotice.diagnostic_code}</Badge>}
          </div>
        )}

        {response && (
          <>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
              {response.generation_status === "extractive_fallback" && (
                <Badge tone="warning">Réponse extractive dégradée</Badge>
              )}
              {response.generation_status === "diagnostic_only" && (
                <Badge tone="warning">Diagnostic sans synthèse</Badge>
              )}
              {response.generation_status !== "generated" && response.diagnostic_code && (
                <Badge>{response.diagnostic_code}</Badge>
              )}
              {response.reused_previous_sources ? (
                <Badge tone="accent">
                  <MessageCircleMore aria-hidden="true" className="size-3" />
                  Sources de l’échange réutilisées
                </Badge>
              ) : (
                <Badge tone="success">
                  <LibraryBig aria-hidden="true" className="size-3" />
                  RAG local · {formatNumber(response.local_result_count)} résultat(s)
                </Badge>
              )}
              {response.external_enrichment_used && (
                <Badge tone="accent">
                  <Cloud aria-hidden="true" className="size-3" />
                  API bibliographique
                </Badge>
              )}
              {facetDrafts.length > 0 && (
                <Badge tone="info">
                  Synthèse en {facetDrafts.length} axe{facetDrafts.length > 1 ? "s" : ""}
                </Badge>
              )}
              {(response.figure_analysis_count ?? 0) > 0 && (
                <Badge tone="info">
                  <ChartNoAxesCombined aria-hidden="true" className="size-3" />
                  {response.figure_analysis_count} figure
                  {response.figure_analysis_count === 1 ? "" : "s"} retenue
                  {response.figure_analysis_count === 1 ? "" : "s"}
                  {response.figure_analysis_duration_seconds
                    ? ` · ${formatResponseTime(response.figure_analysis_duration_seconds * 1000)}`
                    : ""}
                </Badge>
              )}
              <Badge>{response.model}</Badge>
              <Badge>
                <Timer aria-hidden="true" className="size-3" />
                Réponse en{" "}
                {formatResponseTime(
                  message.responseTimeMilliseconds ?? response.duration_seconds * 1000,
                )}
              </Badge>
            </div>

            {response.sources.length > 0 && (
              <details className="mt-4 rounded-xl bg-slate-50 p-3">
                <summary className="cursor-pointer text-xs font-bold text-slate-700">
                  {response.sources.length} source(s) citée(s)
                </summary>
                <div className="mt-3 space-y-3">
                  {response.sources.map((source) => (
                    <div
                      className="border-t border-slate-200 pt-3 first:border-0 first:pt-0"
                      key={source.record_id}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold leading-5 text-slate-800">
                            {source.title}
                          </p>
                          <p className="mt-1 text-[11px] text-slate-500">
                            {source.journal ?? "Journal inconnu"}
                            {source.publication_year ? ` · ${source.publication_year}` : ""}
                          </p>
                        </div>
                        {source.url && (
                          <a
                            aria-label={`Ouvrir la source ${source.title}`}
                            className="grid size-8 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-white hover:text-forest-700"
                            href={source.url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            <ExternalLink aria-hidden="true" className="size-3.5" />
                          </a>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <Badge tone={source.origin === "local_rag" ? "success" : "accent"}>
                          {sourceOriginLabel(source)}
                        </Badge>
                        <Badge tone={source.evidence_level === "full_text" ? "success" : "neutral"}>
                          {sourceEvidenceLabel(source)}
                        </Badge>
                        {source.page_ranges.length > 0 && (
                          <Badge>
                            {source.page_ranges.length === 1 ? "Page" : "Pages"}{" "}
                            {source.page_ranges.join(", ")}
                          </Badge>
                        )}
                        {(source.figure_refs ?? []).map((figure) => (
                          <Badge key={figure} tone="info">
                            {figure}
                          </Badge>
                        ))}
                        {source.doi && <Badge>{source.doi}</Badge>}
                        {source.providers.map((provider) => (
                          <Badge key={provider}>{provider}</Badge>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {response.warnings.length > 0 && (
              <ul className="mt-3 space-y-1 text-xs leading-5 text-amber-700">
                {response.warnings.map((warning) => (
                  <li key={warning}>• {warning}</li>
                ))}
              </ul>
            )}
          </>
        )}
        {assistant && onFeedback && message.id !== "welcome" && (
          <div className="mt-3 flex items-center gap-1 border-t border-slate-100 pt-2">
            <span className="mr-1 text-[10px] text-slate-400">Cette réponse vous aide ?</span>
            <button
              aria-label="Réponse utile"
              className={cn(
                "grid size-7 place-items-center rounded-lg text-slate-400 hover:bg-forest-50 hover:text-forest-700",
                message.helpful === true && "bg-forest-100 text-forest-700",
              )}
              onClick={() => onFeedback(message.id, true)}
              type="button"
            >
              <ThumbsUp aria-hidden="true" className="size-3.5" />
            </button>
            <button
              aria-label="Réponse pas utile"
              className={cn(
                "grid size-7 place-items-center rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-700",
                message.helpful === false && "bg-red-50 text-red-700",
              )}
              onClick={() => onFeedback(message.id, false)}
              type="button"
            >
              <ThumbsDown aria-hidden="true" className="size-3.5" />
            </button>
          </div>
        )}
      </div>
      {!assistant && (
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-cider-500 text-white shadow-soft">
          <UserRound aria-hidden="true" className="size-4" />
        </span>
      )}
    </article>
  );
}
