import { useState, type FormEventHandler, type ReactNode } from "react";

import { Cloud, Microscope, Plus, Send } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { CardBody } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

import { ChatModeMenu } from "./ChatModeMenu";
import { suggestions } from "./conversationView";
import type { ChatInteractionMode } from "./durableChat";

interface ChatComposerProps {
  disabled: boolean;
  draft: string;
  error: string | null;
  externalSources: boolean;
  allowExternalSources: boolean;
  deepResearch: boolean;
  allowDeepResearch: boolean;
  interactionMode: ChatInteractionMode;
  conversationContextAvailable: boolean;
  showSuggestions: boolean;
  onDraftChange: (value: string) => void;
  onExternalSourcesChange: (value: boolean) => void;
  onDeepResearchChange: (value: boolean) => void;
  onInteractionModeChange: (value: ChatInteractionMode) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
}

export function ChatComposer({
  disabled,
  draft,
  error,
  externalSources,
  allowExternalSources,
  deepResearch,
  allowDeepResearch,
  interactionMode,
  conversationContextAvailable,
  showSuggestions,
  onDraftChange,
  onExternalSourcesChange,
  onDeepResearchChange,
  onInteractionModeChange,
  onSubmit,
}: ChatComposerProps) {
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  const chooseMode = (mode: ChatInteractionMode) => {
    onInteractionModeChange(mode);
    setModeMenuOpen(false);
  };

  return (
    <CardBody className="space-y-4 border-t border-slate-200">
      {showSuggestions && (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <button
              className="rounded-full border border-slate-200 bg-white px-3 py-2 text-left text-xs font-medium text-slate-600 transition hover:border-forest-300 hover:bg-forest-50 hover:text-forest-800 focus-visible:ring-2 focus-visible:ring-forest-600"
              key={suggestion}
              onClick={() => onDraftChange(suggestion)}
              type="button"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
          role="alert"
        >
          {error}
        </div>
      )}

      <form className="space-y-3" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="chatbot-question">
          Votre question scientifique
        </label>
        <div
          aria-label="Composer une demande"
          className="rounded-2xl border border-slate-200 bg-white shadow-soft transition hover:border-slate-300"
          role="group"
        >
          <textarea
            autoFocus
            className="min-h-[86px] w-full appearance-none resize-none rounded-t-2xl border-0 bg-transparent px-4 py-3 text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:outline-none focus:ring-0 focus-visible:outline-none focus-visible:ring-0"
            disabled={disabled}
            id="chatbot-question"
            maxLength={4000}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={
              interactionMode === "conversation"
                ? "Demandez un détail, une reformulation ou un autre format…"
                : "Écrivez votre demande en langage naturel…"
            }
            value={draft}
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="relative shrink-0">
              {modeMenuOpen && (
                <ChatModeMenu
                  conversationContextAvailable={conversationContextAvailable}
                  interactionMode={interactionMode}
                  onChoose={chooseMode}
                  onEscape={() => setModeMenuOpen(false)}
                />
              )}
              <Button
                aria-expanded={modeMenuOpen}
                aria-haspopup="menu"
                aria-label="Changer de mode"
                className="rounded-md border-0 bg-transparent shadow-none"
                disabled={disabled}
                onClick={() => setModeMenuOpen((open) => !open)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setModeMenuOpen(false);
                }}
                size="icon"
                title="Changer de mode"
                type="button"
                variant="ghost"
              >
                <Plus
                  aria-hidden="true"
                  className={`size-5 transition-transform ${modeMenuOpen ? "rotate-45" : ""}`}
                />
              </Button>
            </div>
            <Button
              aria-label="Envoyer la question"
              className="h-[42px] px-5"
              disabled={disabled || draft.trim().length < 2}
              type="submit"
            >
              <Send aria-hidden="true" className="size-4" />
              <span className="hidden sm:inline">Envoyer</span>
            </Button>
          </div>
        </div>

        {allowDeepResearch && (
          <ChatOptionToggle
            checked={deepResearch}
            description="Recherche le texte intégral local, vérifie les affirmations et affiche les pages."
            disabled={disabled}
            icon={<Microscope aria-hidden="true" className="size-3.5" />}
            label="Analyse approfondie"
            onChange={onDeepResearchChange}
            tone="forest"
          />
        )}

        {allowExternalSources && (
          <ChatOptionToggle
            checked={externalSources}
            description="Interroge les sources officielles configurées sans importer automatiquement les résultats dans le RAG."
            disabled={disabled}
            icon={<Cloud aria-hidden="true" className="size-3.5" />}
            label="Compléter avec les APIs bibliographiques"
            onChange={onExternalSourcesChange}
            tone="neutral"
          />
        )}
      </form>
    </CardBody>
  );
}

function ChatOptionToggle({
  checked,
  description,
  disabled,
  icon,
  label,
  onChange,
  tone,
}: {
  checked: boolean;
  description: string;
  disabled: boolean;
  icon: ReactNode;
  label: string;
  onChange: (checked: boolean) => void;
  tone: "forest" | "neutral";
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 text-xs",
        tone === "forest"
          ? "border-forest-200 bg-forest-50 text-forest-800"
          : "border-slate-200 bg-slate-50 text-slate-600",
      )}
    >
      <input
        checked={checked}
        className="mt-0.5 size-4 accent-forest-700"
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span>
        <span
          className={cn(
            "flex items-center gap-1.5 font-bold",
            tone === "neutral" && "text-slate-700",
          )}
        >
          {icon} {label}
        </span>
        <span className="mt-1 block leading-5">{description}</span>
      </span>
    </label>
  );
}
