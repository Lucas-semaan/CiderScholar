import { useEffect, useRef } from "react";
import { BookOpenCheck, ExternalLink, FileText, X } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import type { ChatbotCitationAnchor, ChatbotCitationEvidence } from "@/types/chat";

function evidenceLocation(evidence: ChatbotCitationEvidence): string | null {
  if (evidence.page_start !== null && evidence.page_end !== null) {
    return evidence.page_start === evidence.page_end
      ? `Page ${evidence.page_start}`
      : `Pages ${evidence.page_start}–${evidence.page_end}`;
  }
  if (
    evidence.section_path &&
    evidence.paragraph_start !== null &&
    evidence.paragraph_end !== null
  ) {
    const paragraphs =
      evidence.paragraph_start === evidence.paragraph_end
        ? `${evidence.paragraph_start}`
        : `${evidence.paragraph_start}–${evidence.paragraph_end}`;
    return `${evidence.section_path} · paragraphe ${paragraphs}`;
  }
  return evidence.section ?? null;
}

function pdfUrl(anchor: ChatbotCitationAnchor): string | null {
  if (!anchor.local_pdf_url) return null;
  const page = anchor.evidence.find((item) => item.page_start !== null)?.page_start;
  return page ? `${anchor.local_pdf_url}#page=${page}` : anchor.local_pdf_url;
}

export function CitationEvidenceDialog({
  anchor,
  onClose,
  returnFocus,
}: {
  anchor: ChatbotCitationAnchor;
  onClose: () => void;
  returnFocus: HTMLElement | null;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const localPdfUrl = pdfUrl(anchor);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      returnFocus?.focus();
    };
  }, [onClose, returnFocus]);

  return (
    <div
      aria-label="Fermer le détail de la preuve"
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-3 backdrop-blur-sm sm:items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
      role="presentation"
    >
      <section
        aria-labelledby={`citation-title-${anchor.citation_id}`}
        aria-modal="true"
        className="max-h-[85vh] w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
        role="dialog"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap gap-1.5">
              <Badge tone={anchor.source_family === "ascocid_knowledge" ? "accent" : "success"}>
                <BookOpenCheck aria-hidden="true" className="size-3" />
                {anchor.source_family === "ascocid_knowledge"
                  ? "Livre AsCoCid"
                  : "Publication scientifique"}
              </Badge>
              <Badge>Preuve citée</Badge>
            </div>
            <h2
              className="text-base font-bold leading-6 text-slate-900"
              id={`citation-title-${anchor.citation_id}`}
            >
              {anchor.title}
            </h2>
            <p className="mt-1 text-xs text-slate-500">{anchor.label}</p>
          </div>
          <button
            aria-label="Fermer"
            className="grid size-9 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-forest-600"
            onClick={onClose}
            ref={closeRef}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </header>

        <div className="max-h-[calc(85vh-150px)] space-y-4 overflow-y-auto px-5 py-4">
          {anchor.evidence.map((evidence) => {
            const location = evidenceLocation(evidence);
            return (
              <article
                className="rounded-xl border border-slate-200 bg-slate-50 p-4"
                key={evidence.evidence_id}
              >
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                  {location && <Badge>{location}</Badge>}
                  {evidence.section && evidence.section !== evidence.section_path && (
                    <span>{evidence.section}</span>
                  )}
                  {evidence.figure_label && <Badge tone="info">{evidence.figure_label}</Badge>}
                </div>
                <blockquote className="mt-3 whitespace-pre-wrap border-l-2 border-forest-500 pl-3 text-sm leading-6 text-slate-700">
                  {evidence.snippet}
                </blockquote>
              </article>
            );
          })}
        </div>

        {(localPdfUrl || anchor.source_url) && (
          <footer className="flex flex-wrap gap-2 border-t border-slate-200 px-5 py-3">
            {localPdfUrl && (
              <a
                className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-forest-700 hover:bg-forest-50 focus-visible:ring-2 focus-visible:ring-forest-600"
                href={localPdfUrl}
                rel="noreferrer"
                target="_blank"
              >
                <FileText aria-hidden="true" className="size-3.5" /> Ouvrir le document local
              </a>
            )}
            {anchor.source_url && (
              <a
                className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-forest-700 hover:bg-forest-50 focus-visible:ring-2 focus-visible:ring-forest-600"
                href={anchor.source_url}
                rel="noreferrer"
                target="_blank"
              >
                <ExternalLink aria-hidden="true" className="size-3.5" /> Ouvrir la provenance
              </a>
            )}
          </footer>
        )}
      </section>
    </div>
  );
}
