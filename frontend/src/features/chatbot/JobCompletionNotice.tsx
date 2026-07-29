import { BellRing, X } from "lucide-react";

import { Button } from "@/components/ui/Button";
import type { DurableJob } from "@/types/api";

interface JobCompletionNoticeProps {
  conversationTitle: string;
  job: DurableJob;
  onDismiss: () => void;
  onOpen: () => void;
}

export function JobCompletionNotice({
  conversationTitle,
  job,
  onDismiss,
  onOpen,
}: JobCompletionNoticeProps) {
  const message =
    job.state === "succeeded"
      ? "La réponse est prête."
      : job.state === "failed"
        ? "La réponse a échoué."
        : "Le travail est terminé.";

  return (
    <aside
      aria-live="polite"
      className="fixed right-4 top-20 z-50 w-[min(360px,calc(100vw-2rem))] rounded-2xl border border-forest-200 bg-white p-4 shadow-panel"
      role="status"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-forest-100 text-forest-700">
          <BellRing aria-hidden="true" className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-slate-900">{message}</p>
          <p className="mt-1 truncate text-xs text-slate-600">{conversationTitle}</p>
        </div>
        <button
          aria-label="Fermer la notification"
          className="grid size-9 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-forest-600"
          onClick={onDismiss}
          type="button"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <Button className="mt-3 w-full" onClick={onOpen} variant="secondary">
        Ouvrir la conversation
      </Button>
    </aside>
  );
}
