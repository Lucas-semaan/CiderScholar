import { useEffect, useMemo, useState } from "react";

import {
  Check,
  MessageSquareText,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Star,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Form";
import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import type { ChatConversationSummary } from "@/types/api";

import { activeJobBadgeLabel, groupConversations } from "./conversationHistory";

interface ConversationSidebarProps {
  conversations: ChatConversationSummary[];
  activeConversationId: string | null;
  open: boolean;
  loading: boolean;
  disabled: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (conversationId: string) => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
  onFavorite: (conversationId: string, favorite: boolean) => Promise<void>;
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  open,
  loading,
  disabled,
  onClose,
  onNew,
  onSelect,
  onRename,
  onDelete,
  onFavorite,
}: ConversationSidebarProps) {
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<ChatConversationSummary | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ChatConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [searchMatches, setSearchMatches] = useState<ChatConversationSummary[] | null>(null);

  useEffect(() => {
    const cleaned = query.trim();
    if (cleaned.length < 2) {
      setSearchMatches(null);
      return;
    }
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      void api.chatbot.searchConversations(cleaned).then(
        (response) => {
          if (!cancelled) setSearchMatches(response.conversations);
        },
        () => {
          if (!cancelled) setSearchMatches(null);
        },
      );
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [query]);

  const groups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("fr");
    const candidates = searchMatches ?? conversations;
    const filtered =
      normalizedQuery && searchMatches === null
        ? candidates.filter((conversation) =>
            conversation.title.toLocaleLowerCase("fr").includes(normalizedQuery),
          )
        : candidates;
    return groupConversations(
      [...filtered].sort((left, right) => Number(right.favorite) - Number(left.favorite)),
    );
  }, [conversations, query, searchMatches]);

  const startRename = (conversation: ChatConversationSummary) => {
    setRenaming(conversation);
    setRenameDraft(conversation.title);
  };

  const submitRename = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = renameDraft.trim();
    if (!renaming || !title) return;
    try {
      await onRename(renaming.id, title);
      setRenaming(null);
    } catch {
      // The parent exposes the actionable error while this editor stays open.
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await onDelete(pendingDelete.id);
      setPendingDelete(null);
    } catch {
      // The parent exposes the actionable error and the confirmation stays open.
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      {open && (
        <button
          aria-label="Fermer l’historique"
          className="fixed inset-0 z-30 bg-slate-900/25 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          type="button"
        />
      )}
      <aside
        aria-label="Historique des conversations"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[300px] shrink-0 flex-col border-r border-slate-200 bg-slate-50 shadow-panel transition-transform lg:sticky lg:top-8 lg:z-auto lg:h-[calc(100vh-96px)] lg:w-[276px] lg:translate-x-0 lg:shadow-none",
          open ? "visible translate-x-0" : "invisible -translate-x-full lg:visible",
        )}
      >
        <div className="border-b border-slate-200 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-bold text-slate-900">Conversations</p>
              <p className="mt-0.5 text-[11px] text-slate-500">Votre historique scientifique</p>
            </div>
            <Button
              aria-label="Fermer l’historique"
              className="lg:hidden"
              onClick={onClose}
              size="icon"
              variant="ghost"
            >
              <X aria-hidden="true" className="size-4" />
            </Button>
          </div>
          <Button
            className="mt-4 w-full justify-start"
            disabled={disabled}
            onClick={onNew}
            variant="secondary"
          >
            <Plus aria-hidden="true" className="size-4" /> Nouvelle conversation
          </Button>
          <label className="relative mt-3 block">
            <span className="sr-only">Rechercher dans les conversations</span>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
            />
            <Input
              className="h-10 min-h-10 bg-white pl-9 text-xs"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Rechercher un chat"
              value={query}
            />
          </label>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-3">
          {loading && (
            <div aria-live="polite" className="space-y-2 px-2" role="status">
              <p className="sr-only">Chargement de l’historique…</p>
              {[0, 1, 2, 3].map((item) => (
                <div className="h-12 animate-pulse rounded-xl bg-slate-200/70" key={item} />
              ))}
            </div>
          )}

          {!loading && groups.length === 0 && (
            <div className="mx-2 mt-5 rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-6 text-center">
              <MessageSquareText aria-hidden="true" className="mx-auto size-5 text-slate-400" />
              <p className="mt-2 text-xs font-semibold text-slate-700">
                {query ? "Aucun chat trouvé" : "Aucune conversation"}
              </p>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                {query ? "Essayez un autre mot-clé." : "Votre premier échange apparaîtra ici."}
              </p>
            </div>
          )}

          {!loading &&
            groups.map((group) => (
              <section className="mb-5" key={group.label}>
                <h2 className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">
                  {group.label}
                </h2>
                <div className="space-y-0.5">
                  {group.conversations.map((conversation) => {
                    const active = conversation.id === activeConversationId;
                    if (renaming?.id === conversation.id) {
                      return (
                        <form
                          className="flex items-center gap-1 rounded-xl bg-white p-1.5 shadow-soft"
                          key={conversation.id}
                          onSubmit={(event) => void submitRename(event)}
                        >
                          <Input
                            aria-label="Nouveau titre"
                            autoFocus
                            className="h-9 min-h-9 px-2 text-xs"
                            maxLength={120}
                            onChange={(event) => setRenameDraft(event.target.value)}
                            value={renameDraft}
                          />
                          <button
                            aria-label="Enregistrer le titre"
                            className="grid size-8 shrink-0 place-items-center rounded-lg text-forest-700 hover:bg-forest-50 focus-visible:ring-2 focus-visible:ring-forest-600"
                            type="submit"
                          >
                            <Check aria-hidden="true" className="size-3.5" />
                          </button>
                          <button
                            aria-label="Annuler le renommage"
                            className="grid size-8 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-forest-600"
                            onClick={() => setRenaming(null)}
                            type="button"
                          >
                            <X aria-hidden="true" className="size-3.5" />
                          </button>
                        </form>
                      );
                    }
                    return (
                      <div
                        className={cn(
                          "group flex min-h-12 items-center rounded-xl transition",
                          active ? "bg-forest-100 text-forest-900" : "hover:bg-white",
                        )}
                        key={conversation.id}
                      >
                        <button
                          className="min-w-0 flex-1 px-3 py-2.5 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-forest-600"
                          disabled={disabled}
                          onClick={() => onSelect(conversation.id)}
                          type="button"
                        >
                          <span className="flex items-center gap-2 text-xs font-semibold">
                            <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
                            {conversation.active_job_count > 0 && (
                              <span
                                aria-label={activeJobBadgeLabel(conversation.active_job_count)}
                                className="grid min-w-5 place-items-center rounded-full bg-cider-200 px-1.5 py-0.5 text-[10px] font-bold text-cider-900"
                                title={activeJobBadgeLabel(conversation.active_job_count)}
                              >
                                {conversation.active_job_count}
                              </span>
                            )}
                          </span>
                          <span className="mt-0.5 block text-[10px] text-slate-400">
                            {Math.ceil(conversation.message_count / 2)} échange(s)
                          </span>
                        </button>
                        <div
                          className={cn(
                            "flex shrink-0 pr-1.5",
                            active
                              ? "opacity-100"
                              : "opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100",
                          )}
                        >
                          <button
                            aria-label={
                              conversation.favorite
                                ? `Retirer « ${conversation.title} » des favoris`
                                : `Ajouter « ${conversation.title} » aux favoris`
                            }
                            className="grid size-8 place-items-center rounded-lg text-slate-500 hover:bg-white hover:text-cider-700 focus-visible:ring-2 focus-visible:ring-cider-600"
                            disabled={disabled}
                            onClick={() => void onFavorite(conversation.id, !conversation.favorite)}
                            type="button"
                          >
                            <Star
                              aria-hidden="true"
                              className={cn(
                                "size-3.5",
                                conversation.favorite && "fill-cider-400 text-cider-700",
                              )}
                            />
                          </button>
                          <button
                            aria-label={`Renommer « ${conversation.title} »`}
                            className="grid size-8 place-items-center rounded-lg text-slate-500 hover:bg-white hover:text-forest-700 focus-visible:ring-2 focus-visible:ring-forest-600"
                            disabled={disabled}
                            onClick={() => startRename(conversation)}
                            type="button"
                          >
                            <Pencil aria-hidden="true" className="size-3.5" />
                          </button>
                          <button
                            aria-label={`Supprimer « ${conversation.title} »`}
                            className="grid size-8 place-items-center rounded-lg text-slate-500 hover:bg-red-50 hover:text-red-700 focus-visible:ring-2 focus-visible:ring-red-600"
                            disabled={disabled}
                            onClick={() => setPendingDelete(conversation)}
                            type="button"
                          >
                            <Trash2 aria-hidden="true" className="size-3.5" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            ))}
        </div>

        <div className="border-t border-slate-200 px-4 py-3 text-[10px] leading-4 text-slate-500">
          <span className="flex items-center gap-1.5 font-semibold text-slate-600">
            <ShieldCheck aria-hidden="true" className="size-3.5 text-forest-600" /> Stockage local
          </span>
          Les conversations restent dans la base SQLite de CiderScholar.
        </div>
      </aside>

      <Dialog
        footer={
          <div className="flex justify-end gap-2">
            <Button disabled={deleting} onClick={() => setPendingDelete(null)} variant="secondary">
              Annuler
            </Button>
            <Button loading={deleting} onClick={() => void confirmDelete()} variant="danger">
              <Trash2 aria-hidden="true" className="size-4" /> Supprimer
            </Button>
          </div>
        }
        onClose={() => !deleting && setPendingDelete(null)}
        open={pendingDelete !== null}
        title="Supprimer cette conversation ?"
      >
        <p className="text-sm leading-6 text-slate-600">
          « {pendingDelete?.title} » et tous ses messages seront définitivement supprimés de la base
          locale.
        </p>
      </Dialog>
    </>
  );
}
