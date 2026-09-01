import { KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { useId, type FormEvent } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Form";
import { ArgoKeyTutorial, ArgoNetworkNotice } from "@/features/onboarding/ArgoKeyTutorial";
import type { LlmProviderId, LlmProviderProfile } from "@/types/api";

interface LlmProviderSettingsCardsProps {
  busy: string | null;
  providers: LlmProviderProfile[];
  onActivate: (provider: LlmProviderId) => void;
  onDelete: (provider: LlmProviderId) => void;
  onSave: (provider: LlmProviderId, event: FormEvent<HTMLFormElement>) => void;
  onTest: (provider: LlmProviderId) => void;
}

export function LlmProviderSettingsCards({
  busy,
  providers,
  onActivate,
  onDelete,
  onSave,
  onTest,
}: LlmProviderSettingsCardsProps) {
  return (
    <section aria-labelledby="llm-providers-title" className="space-y-4">
      <div>
        <h2 className="font-bold text-slate-900" id="llm-providers-title">
          Fournisseur LLM
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Choisissez un seul fournisseur. Les clés restent chiffrées pour ce compte Windows.
        </p>
      </div>
      <div className="space-y-4" role="radiogroup" aria-label="Fournisseur LLM actif">
        {providers.map((provider) => (
          <ProviderCard
            busy={busy}
            key={provider.id}
            onActivate={onActivate}
            onDelete={onDelete}
            onSave={onSave}
            onTest={onTest}
            provider={provider}
          />
        ))}
      </div>
    </section>
  );
}

function ProviderCard({
  busy,
  onActivate,
  onDelete,
  onSave,
  onTest,
  provider,
}: Omit<LlmProviderSettingsCardsProps, "providers"> & { provider: LlmProviderProfile }) {
  const radioId = useId();
  const isArgo = provider.id === "argo";
  const prefix = `llm-provider-${provider.id}`;
  const saving = busy === `${prefix}-save`;

  return (
    <Card className={provider.active ? "border-forest-300" : undefined}>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-bold text-slate-900">{provider.label}</h3>
            <p className="mt-1 text-xs text-slate-500">
              {isArgo
                ? "Endpoint ARGO INRAE prédéfini. La valeur enregistrée ne peut jamais être relue."
                : "Serveur compatible OpenAI : endpoint HTTPS, clé API et modèle requis."}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Badge tone={provider.key_configured ? "success" : "warning"}>
              {provider.key_configured ? "Configurée" : "Clé absente"}
            </Badge>
            <input
              aria-label={`Utiliser ${provider.label}`}
              checked={provider.active}
              className="size-5 accent-forest-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-forest-600"
              disabled={busy !== null}
              id={radioId}
              name="active-llm-provider"
              onChange={() => onActivate(provider.id)}
              type="radio"
              value={provider.id}
            />
          </div>
        </div>
      </CardHeader>
      <CardBody>
        <form className="grid gap-4" onSubmit={(event) => onSave(provider.id, event)}>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label="Endpoint API"
              hint={
                isArgo
                  ? "Endpoint imposé par ARGO INRAE."
                  : "HTTPS uniquement, sans identifiants, paramètres ni fragment."
              }
            >
              <Input
                autoComplete="url"
                defaultValue={provider.base_url}
                name="base_url"
                readOnly={!provider.endpoint_editable}
                required
                type="url"
              />
            </Field>
            <Field
              label="Modèle"
              hint={isArgo ? "Modèle configuré pour ARGO." : "Nom exact exposé par le fournisseur."}
            >
              <Input
                defaultValue={provider.model}
                name="model"
                readOnly={isArgo}
                required
                type="text"
              />
            </Field>
          </div>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
            <Field
              label={provider.key_configured ? "Nouvelle clé API" : "Clé API"}
              hint="La saisie remplace la clé actuelle sans jamais l’afficher."
            >
              <Input
                autoComplete="off"
                maxLength={4098}
                name="key"
                placeholder="Coller une clé personnelle"
                required={!provider.key_configured}
                type="password"
              />
            </Field>
            <div className="flex flex-wrap items-end gap-3">
              <Button loading={saving} type="submit">
                <KeyRound aria-hidden="true" className="size-4" /> Enregistrer
              </Button>
              <Button
                disabled={!provider.key_configured}
                loading={busy === `${prefix}-test`}
                onClick={() => onTest(provider.id)}
                type="button"
                variant="secondary"
              >
                <RefreshCw aria-hidden="true" className="size-4" /> Tester
              </Button>
              <Button
                disabled={!provider.key_configured}
                loading={busy === `${prefix}-delete`}
                onClick={() => onDelete(provider.id)}
                type="button"
                variant="danger"
              >
                <Trash2 aria-hidden="true" className="size-4" /> Supprimer
              </Button>
            </div>
          </div>
        </form>
        {isArgo && (
          <div className="mt-4 space-y-3">
            <ArgoNetworkNotice />
            <ArgoKeyTutorial />
          </div>
        )}
      </CardBody>
    </Card>
  );
}
