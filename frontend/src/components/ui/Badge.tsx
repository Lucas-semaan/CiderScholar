import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "accent";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-slate-100 text-slate-500 ring-slate-200",
  success: "bg-forest-600/10 text-forest-600 ring-forest-600/15",
  warning: "bg-cider-500/10 text-cider-600 ring-cider-500/15",
  danger: "bg-red-700/10 text-red-700 ring-red-700/15",
  info: "bg-sky-600/10 text-sky-700 ring-sky-600/15",
  accent: "bg-cider-500/10 text-cider-600 ring-cider-500/15",
};

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-[0.02em] ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
