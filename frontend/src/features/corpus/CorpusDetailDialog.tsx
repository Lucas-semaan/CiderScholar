import type { ReactNode } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import type { CorpusArticle } from "@/types/api";

interface CorpusDetailDialogProps {
  article: CorpusArticle | null;
  title: string;
  loadingLabel: string;
  error: string | null;
  loading: boolean;
  onClose: () => void;
  retry: () => void;
  children: ReactNode;
}

/** Shared shell for the four read-only corpus detail views. */
export function CorpusDetailDialog({
  article,
  title,
  loadingLabel,
  error,
  loading,
  onClose,
  retry,
  children,
}: CorpusDetailDialogProps) {
  return (
    <Dialog
      onClose={onClose}
      open={article !== null}
      title={article ? `${title} — ${article.title}` : title}
    >
      {loading && <LoadingState label={loadingLabel} />}
      {error && <ErrorState message={error} retry={retry} />}
      {children}
    </Dialog>
  );
}
