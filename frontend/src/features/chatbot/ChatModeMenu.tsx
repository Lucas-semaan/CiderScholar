import type { LucideIcon } from "lucide-react";
import { BookOpenText, Check, MessageCircleMore, Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";

import type { ChatInteractionMode } from "./durableChat";

interface ChatModeMenuProps {
  conversationContextAvailable: boolean;
  interactionMode: ChatInteractionMode;
  onChoose: (mode: ChatInteractionMode) => void;
  onEscape: () => void;
}

interface ModeOption {
  description: string;
  disabledDescription?: string;
  icon: LucideIcon;
  iconClassName: string;
  label: string;
  mode: ChatInteractionMode;
}

const modeOptions: ModeOption[] = [
  {
    description: "Comprend l’intention de votre demande.",
    icon: Sparkles,
    iconClassName: "text-cider-600",
    label: "Automatique",
    mode: "auto",
  },
  {
    description: "Cherche de nouvelles sources pour répondre.",
    icon: BookOpenText,
    iconClassName: "text-forest-700",
    label: "Recherche bibliographique",
    mode: "research",
  },
  {
    description: "Détaille ou reformule sans relancer la recherche.",
    disabledDescription: "Disponible après une première réponse sourcée.",
    icon: MessageCircleMore,
    iconClassName: "text-sky-700",
    label: "Chat sur les résultats",
    mode: "conversation",
  },
];

export function ChatModeMenu({
  conversationContextAvailable,
  interactionMode,
  onChoose,
  onEscape,
}: ChatModeMenuProps) {
  return (
    <div
      aria-label="Choisir le mode de réponse"
      className="absolute bottom-full left-0 z-20 mb-2 w-[min(330px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl"
      onKeyDown={(event) => {
        if (event.key === "Escape") onEscape();
      }}
      role="menu"
    >
      {modeOptions.map((option) => {
        const disabled = option.mode === "conversation" && !conversationContextAvailable;
        const selected = interactionMode === option.mode;
        const Icon = option.icon;

        return (
          <button
            aria-checked={selected}
            className="flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition enabled:hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-forest-600 disabled:cursor-not-allowed disabled:opacity-45"
            disabled={disabled}
            key={option.mode}
            onClick={() => onChoose(option.mode)}
            role="menuitemradio"
            type="button"
          >
            <Icon aria-hidden="true" className={cn("mt-0.5 size-4", option.iconClassName)} />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-bold text-slate-800">{option.label}</span>
              <span className="mt-0.5 block text-xs leading-5 text-slate-500">
                {disabled ? option.disabledDescription : option.description}
              </span>
            </span>
            {selected && <Check aria-hidden="true" className="mt-0.5 size-4 text-forest-700" />}
          </button>
        );
      })}
    </div>
  );
}
