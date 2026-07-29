import type { LucideIcon } from "lucide-react";

import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

export function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  tone = "forest",
  to,
  actionLabel,
}: {
  label: string;
  value: string;
  note: string;
  icon: LucideIcon;
  tone?: "forest" | "cider" | "sky" | "slate";
  to?: string;
  actionLabel?: string;
}) {
  const tones = {
    forest: "bg-forest-600/10 text-forest-600",
    cider: "bg-cider-500/10 text-cider-600",
    sky: "bg-sky-50 text-sky-700",
    slate: "bg-slate-100 text-slate-600",
  };
  const content = (
    <Card
      className={cn(
        "h-full p-5",
        to &&
          "transition group-hover:-translate-y-0.5 group-hover:border-forest-200 group-hover:shadow-panel group-focus-visible:border-forest-300",
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className={cn("grid size-10 place-items-center rounded-[10px]", tones[tone])}>
          <Icon aria-hidden="true" className="size-5" />
        </div>
        {to && (
          <ArrowUpRight
            aria-hidden="true"
            className="size-4 text-slate-300 transition group-hover:text-forest-500"
          />
        )}
      </div>
      <p className="mt-6 text-3xl font-bold tracking-[-0.02em] text-ink-950">{value}</p>
      <p className="mt-1 text-sm font-semibold text-slate-700">{label}</p>
      <p className="mt-2 text-xs leading-5 text-slate-500">{note}</p>
    </Card>
  );

  if (!to) return content;

  return (
    <Link
      aria-label={actionLabel ?? `Ouvrir ${label}`}
      className="group block h-full rounded-[14px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500 focus-visible:ring-offset-2"
      to={to}
    >
      {content}
    </Link>
  );
}
