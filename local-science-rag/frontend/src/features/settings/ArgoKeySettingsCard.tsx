import { KeyRound, RefreshCw, Trash2 } from "lucide-react";
import type { FormEventHandler } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Form";
import { ArgoKeyTutorial, ArgoNetworkNotice } from "@/features/onboarding/ArgoKeyTutorial";

interface ArgoKeySettingsCardProps {
  busy: string | null;
  configured: boolean;
  onDelete: () => void;
  onSave: FormEventHandler<HTMLFormElement>;
  onTest: () => void;
}

export function ArgoKeySettingsCard({
  busy,
  configured,
  onDelete,
  onSave,
  onTest,
}: ArgoKeySettingsCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-slate-900">Clé ARGO personnelle</h2>
            <p className="mt-1 text-xs text-slate-500">
              Chiffrée avec Windows DPAPI. La valeur enregistrée ne peut jamais être relue.
            </p>
          </div>
          <Badge tone={configured ? "success" : "warning"}>
            {configured ? "Configurée" : "Absente"}
          </Badge>
        </div>
      </CardHeader>
      <CardBody>
        <form className="grid gap-4 lg:grid-cols-[1fr_auto]" onSubmit={onSave}>
          <Field
            label={configured ? "Nouvelle clé" : "Clé API ARGO"}
            hint="La saisie remplace la clé actuelle sans jamais l’afficher."
          >
            <Input
              autoComplete="off"
              maxLength={4098}
              name="argo_key"
              placeholder="Coller une clé personnelle"
              required
              type="password"
            />
          </Field>
          <div className="flex flex-wrap items-end gap-3">
            <Button loading={busy === "argo-key-save"} type="submit">
              <KeyRound aria-hidden="true" className="size-4" />{" "}
              {configured ? "Remplacer" : "Enregistrer"}
            </Button>
            <Button
              disabled={!configured}
              loading={busy === "argo-key-test"}
              onClick={onTest}
              type="button"
              variant="secondary"
            >
              <RefreshCw aria-hidden="true" className="size-4" /> Tester la connexion
            </Button>
            <Button
              disabled={!configured}
              loading={busy === "argo-key-delete"}
              onClick={onDelete}
              type="button"
              variant="danger"
            >
              <Trash2 aria-hidden="true" className="size-4" /> Supprimer
            </Button>
          </div>
        </form>
        <div className="mt-4 space-y-3">
          <ArgoNetworkNotice />
          <ArgoKeyTutorial />
        </div>
      </CardBody>
    </Card>
  );
}
