import {
  BookOpenCheck,
  Bot,
  Download,
  History,
  MessageSquarePlus,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

export function ChatbotHero() {
  return (
    <section className="overflow-hidden rounded-[20px] bg-gradient-to-br from-forest-800 via-forest-700 to-slate-900 px-5 py-5 text-white shadow-soft sm:px-7 sm:py-6">
      <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-cider-300">
            <Sparkles aria-hidden="true" className="size-4" /> Assistant scientifique
          </div>
          <h1 className="mt-3 text-2xl font-extrabold tracking-[-0.035em] sm:text-3xl">
            Que souhaitez-vous explorer ?
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-forest-50/80">
            Interrogez le RAG local et retrouvez chaque échange dans votre historique.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge className="border-white/15 bg-white/10 text-white">
            <BookOpenCheck aria-hidden="true" className="size-3.5" /> RAG local
          </Badge>
          <Badge className="border-white/15 bg-white/10 text-white">
            <Bot aria-hidden="true" className="size-3.5" /> ARGO INRAE
          </Badge>
          <Badge className="border-white/15 bg-white/10 text-white">
            <ShieldCheck aria-hidden="true" className="size-3.5" /> Sources vérifiées
          </Badge>
        </div>
      </div>
    </section>
  );
}

export function ConversationHeader({
  active,
  disabled,
  title,
  onExport,
  onHistory,
  onNew,
}: {
  active: boolean;
  disabled: boolean;
  title: string;
  onExport: (format: "markdown" | "pdf") => void;
  onHistory: () => void;
  onNew: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/70 px-4 py-3 sm:px-5">
      <div className="flex min-w-0 items-center gap-2">
        <Button
          aria-label="Ouvrir l’historique"
          className="lg:hidden"
          onClick={onHistory}
          size="icon"
          variant="ghost"
        >
          <History aria-hidden="true" className="size-4" />
        </Button>
        <div className="min-w-0">
          <p className="truncate text-sm font-bold text-slate-800">{title}</p>
          <p className="mt-0.5 hidden text-xs text-slate-500 sm:block">
            Les réponses reposent sur des sources qualifiées et traçables.
          </p>
        </div>
      </div>
      <Button aria-label="Nouveau chat" disabled={disabled} onClick={onNew} variant="ghost">
        <MessageSquarePlus aria-hidden="true" className="size-4" />
        <span className="hidden sm:inline">Nouveau chat</span>
      </Button>
      {active && (
        <div className="flex">
          <Button
            aria-label="Exporter en Markdown"
            disabled={disabled}
            onClick={() => onExport("markdown")}
            variant="ghost"
          >
            <Download aria-hidden="true" className="size-4" /> MD
          </Button>
          <Button
            aria-label="Exporter en PDF"
            disabled={disabled}
            onClick={() => onExport("pdf")}
            variant="ghost"
          >
            <Download aria-hidden="true" className="size-4" /> PDF
          </Button>
        </div>
      )}
    </div>
  );
}
