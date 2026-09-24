import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { api } from "@/lib/api";

const minimumLength = 10;

export function ExpertCorrectionDialog({
  messageId,
  messageContent,
  onClose,
}: {
  messageId: string;
  messageContent: string;
  onClose: () => void;
}) {
  const [problem, setProblem] = useState("");
  const [proposedCorrection, setProposedCorrection] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [scope, setScope] = useState<"this_answer" | "reusable_method">("this_answer");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedProblem = problem.trim();
    const trimmedCorrection = proposedCorrection.trim();
    const trimmedSelection = selectedText.trim();
    if (trimmedProblem.length < minimumLength || trimmedCorrection.length < minimumLength) {
      setError(`Décrivez le problème et la correction en au moins ${minimumLength} caractères.`);
      return;
    }
    if (trimmedSelection && !messageContent.includes(trimmedSelection)) {
      setError("Le passage sélectionné doit appartenir à la réponse.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await api.chatbot.submitExpertCorrection(messageId, {
        client_request_id: crypto.randomUUID(),
        selected_text: trimmedSelection,
        problem: trimmedProblem,
        proposed_correction: trimmedCorrection,
        scope,
      });
      setSubmitted(true);
    } catch (caught: unknown) {
      setError(
        caught instanceof Error ? caught.message : "La correction n’a pas pu être enregistrée.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Proposer une correction"
      footer={
        submitted ? (
          <Button onClick={onClose}>Fermer</Button>
        ) : (
          <div className="flex justify-end gap-2">
            <Button onClick={onClose} variant="secondary">
              Annuler
            </Button>
            <Button form="expert-correction-form" loading={submitting} type="submit">
              Enregistrer la proposition
            </Button>
          </div>
        )
      }
    >
      {submitted ? (
        <div className="space-y-3" role="status">
          <p className="font-semibold text-forest-800">Correction enregistrée.</p>
          <p className="text-sm leading-6 text-slate-600">
            Elle reste une proposition privée et ne modifie pas la mémoire experte.
          </p>
        </div>
      ) : (
        <form className="space-y-5" id="expert-correction-form" onSubmit={submit}>
          <p className="text-sm leading-6 text-slate-600">
            Signalez un problème observable dans cette réponse. Une revue séparée est nécessaire
            avant toute modification durable.
          </p>
          <div>
            <label className="text-sm font-semibold text-slate-800" htmlFor="correction-problem">
              Problème observé
            </label>
            <textarea
              className="mt-2 min-h-24 w-full rounded-[10px] border border-slate-300 px-3 py-2 text-sm outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
              id="correction-problem"
              onChange={(event) => setProblem(event.target.value)}
              placeholder="Quelle partie de la réponse doit être corrigée ?"
              value={problem}
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-slate-800" htmlFor="correction-proposal">
              Correction proposée
            </label>
            <textarea
              className="mt-2 min-h-24 w-full rounded-[10px] border border-slate-300 px-3 py-2 text-sm outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
              id="correction-proposal"
              onChange={(event) => setProposedCorrection(event.target.value)}
              placeholder="Quelle formulation ou règle devrait être examinée ?"
              value={proposedCorrection}
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-slate-800" htmlFor="correction-selection">
              Passage concerné (facultatif)
            </label>
            <textarea
              className="mt-2 min-h-20 w-full rounded-[10px] border border-slate-300 px-3 py-2 text-sm outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
              id="correction-selection"
              onChange={(event) => setSelectedText(event.target.value)}
              placeholder="Copiez un extrait exact de la réponse si utile."
              value={selectedText}
            />
          </div>
          <fieldset>
            <legend className="text-sm font-semibold text-slate-800">Portée à examiner</legend>
            <div className="mt-2 space-y-2 text-sm text-slate-700">
              <label className="flex items-center gap-2">
                <input
                  checked={scope === "this_answer"}
                  name="correction-scope"
                  onChange={() => setScope("this_answer")}
                  type="radio"
                />
                Cette réponse uniquement
              </label>
              <label className="flex items-center gap-2">
                <input
                  checked={scope === "reusable_method"}
                  name="correction-scope"
                  onChange={() => setScope("reusable_method")}
                  type="radio"
                />
                Méthode réutilisable à revoir
              </label>
            </div>
          </fieldset>
          {error && (
            <p className="text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </Dialog>
  );
}
