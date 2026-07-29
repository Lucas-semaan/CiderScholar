import type { LucideIcon } from "lucide-react";

import { AlertCircle, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/Button";

export function LoadingState({ label = "Chargement des données…" }: { label?: string }) {
  return (
    <div className="grid min-h-56 place-items-center rounded-[14px] border border-dashed border-slate-200 bg-white/60">
      <div className="flex items-center gap-3 text-sm font-medium text-slate-500">
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin text-forest-600" />
        {label}
      </div>
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="rounded-[14px] border border-red-200 bg-red-50 p-5 text-red-800">
      <div className="flex items-start gap-3">
        <AlertCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
        <div>
          <p className="font-semibold">Impossible de charger cette section</p>
          <p className="mt-1 text-sm text-red-700">{message}</p>
          {retry && (
            <Button className="mt-4" onClick={retry} variant="secondary">
              Réessayer
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="grid min-h-56 place-items-center rounded-[14px] border border-dashed border-slate-200 bg-white/60 p-8 text-center">
      <div className="max-w-sm">
        <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-slate-100 text-slate-500">
          <Icon aria-hidden="true" className="size-6" />
        </div>
        <h3 className="mt-4 font-semibold text-slate-800">{title}</h3>
        <p className="mt-2 text-sm leading-6 text-slate-500">{description}</p>
      </div>
    </div>
  );
}
