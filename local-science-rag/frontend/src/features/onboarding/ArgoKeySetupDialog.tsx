import { useState, type FormEvent } from "react";

import { KeyRound, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/Form";
import { api } from "@/lib/api";

import { ArgoKeyTutorial, ArgoNetworkNotice } from "./ArgoKeyTutorial";

export function ArgoKeySetupDialog({
  open,
  onClose,
  onConfigured,
}: {
  open: boolean;
  onClose: () => void;
  onConfigured: () => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const verifyAndSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const candidate = key.trim();
    if (!candidate) return;
    setBusy(true);
    setError(null);
    try {
      await api.argoKey.save(candidate);
      const result = await api.argoKey.test();
      if (result.state !== "ready") {
        await api.argoKey.remove();
        setError(result.message);
        return;
      }
      setKey("");
      onConfigured();
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "La clé n’a pas pu être vérifiée.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} title="Configurer votre clé ARGO" onClose={onClose}>
      <form className="space-y-5" onSubmit={(event) => void verifyAndSave(event)}>
        <div className="flex gap-3 rounded-xl border border-forest-600/15 bg-forest-600/5 p-4">
          <ShieldCheck aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-forest-600" />
          <p className="text-sm leading-6 text-slate-600">
            Votre clé personnelle est chiffrée avec Windows DPAPI et reste sur ce poste. Elle n’est
            jamais affichée après l’enregistrement.
          </p>
        </div>
        <Field
          label="Clé API ARGO"
          hint="Collez la clé complète obtenue dans les réglages de votre compte ARGO."
        >
          <Input
            autoComplete="off"
            autoFocus
            maxLength={4098}
            onChange={(event) => setKey(event.target.value)}
            placeholder="Clé personnelle"
            type="password"
            value={key}
          />
        </Field>
        <ArgoKeyTutorial />
        <ArgoNetworkNotice />
        {error && (
          <p
            className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            role="alert"
          >
            {error}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <Button onClick={onClose} type="button" variant="secondary">
            Plus tard
          </Button>
          <Button disabled={!key.trim()} loading={busy} type="submit">
            <KeyRound aria-hidden="true" className="size-4" /> Vérifier et enregistrer
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
