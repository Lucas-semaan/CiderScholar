import { useCallback, useState } from "react";

import {
  BookOpenText,
  BotMessageSquare,
  FlaskConical,
  HeartPulse,
  Menu,
  MessageSquareWarning,
  Settings,
  X,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";

const navigation = [
  { to: "/", label: "Assistant scientifique", icon: BotMessageSquare, end: true },
  { to: "/bibliotheque", label: "Base documentaire", icon: BookOpenText },
  { to: "/syntheses", label: "Synthèses", icon: FlaskConical },
  { to: "/diagnostic", label: "Diagnostic", icon: HeartPulse },
  { to: "/retours-pilote", label: "Retours pilote", icon: MessageSquareWarning },
  { to: "/parametres", label: "Paramètres", icon: Settings },
];

export function AppShell() {
  const [mobileNavigation, setMobileNavigation] = useState(false);
  const loadRuntime = useCallback(() => api.system.settings(), []);
  const runtime = useRemoteData(loadRuntime);

  return (
    <div className="min-h-screen bg-stone-50">
      {mobileNavigation && (
        <button
          aria-label="Fermer la navigation"
          className="fixed inset-0 z-30 bg-slate-900/25 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileNavigation(false)}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[280px] flex-col border-r border-slate-200 bg-white text-slate-900 shadow-soft transition-transform lg:translate-x-0",
          mobileNavigation ? "visible translate-x-0" : "invisible -translate-x-full lg:visible",
        )}
      >
        <div className="flex h-20 items-center justify-between border-b border-slate-200 px-5">
          <NavLink
            className="flex items-center gap-3"
            onClick={() => setMobileNavigation(false)}
            to="/"
          >
            <span className="grid size-10 place-items-center rounded-xl bg-cider-500 text-sm font-extrabold tracking-tight text-white shadow-soft">
              CS
            </span>
            <span>
              <span className="block text-base font-bold tracking-[-0.01em] text-slate-900">
                Cider<span className="text-cider-500">Scholar</span>
              </span>
              <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-forest-600">
                Science cidricole
              </span>
            </span>
          </NavLink>
          <Button
            aria-label="Fermer"
            className="lg:hidden"
            onClick={() => setMobileNavigation(false)}
            size="icon"
            variant="ghost"
          >
            <X aria-hidden="true" className="size-5" />
          </Button>
        </div>

        <nav aria-label="Navigation principale" className="flex-1 space-y-1 px-3 py-6">
          <p className="mb-3 px-3 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Espace de travail
          </p>
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              className={({ isActive }) =>
                cn(
                  "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition",
                  isActive
                    ? "bg-forest-600/8 font-semibold text-forest-600"
                    : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
                )
              }
              end={end ?? false}
              key={to}
              onClick={() => setMobileNavigation(false)}
              to={to}
            >
              <Icon aria-hidden="true" className="size-[18px]" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="m-3 rounded-2xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-slate-500">Moteur actif</span>
            <span className="size-2 rounded-full bg-forest-600 shadow-[0_0_0_4px_rgba(98,141,23,.1)]" />
          </div>
          <p className="mt-2 truncate text-sm font-bold text-slate-900">
            {runtime.data?.llm_model ?? "Chargement…"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {runtime.data?.llm_provider.toUpperCase() ?? "—"} · SQLite local
          </p>
        </div>
      </aside>

      <div className="lg:pl-[280px]">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur-md sm:px-7 lg:px-10">
          <div className="flex items-center gap-3">
            <Button
              aria-label="Ouvrir la navigation"
              className="lg:hidden"
              onClick={() => setMobileNavigation(true)}
              size="icon"
              variant="secondary"
            >
              <Menu aria-hidden="true" className="size-5" />
            </Button>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <span className="font-semibold text-slate-700">Espace local</span>
              <span>/</span>
              <span>{runtime.data?.database_name ?? "science_rag.sqlite3"}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={runtime.data?.offline_mode ? "neutral" : "success"}>
              <span className="size-1.5 rounded-full bg-current" />
              {runtime.data?.offline_mode ? "Hors ligne" : "Réseau maîtrisé"}
            </Badge>
            {runtime.data?.llm_provider === "argo" && <Badge tone="accent">ARGO INRAE</Badge>}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] px-4 py-7 sm:px-7 lg:px-10 lg:py-10">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
