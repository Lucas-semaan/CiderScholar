import { KeyRound, RefreshCw, ScanSearch, Trash2 } from "lucide-react";
import type { FormEventHandler } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select, Textarea } from "@/components/ui/Form";
import type { RuntimeSettings } from "@/types/api";

interface PublisherAccessCardProps {
  busy: string | null;
  onDeleteCredentials: () => void;
  onRefreshRun: () => void;
  onSaveCredentials: FormEventHandler<HTMLFormElement>;
  onStartRun: FormEventHandler<HTMLFormElement>;
  runId: string | null;
  runState: string | null;
  settings: RuntimeSettings;
}

export function PublisherAccessCard({
  busy,
  onDeleteCredentials,
  onRefreshRun,
  onSaveCredentials,
  onStartRun,
  runId,
  runState,
  settings,
}: PublisherAccessCardProps) {
  const access = settings.publisher_access;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-slate-900">Test d’accès publishers autorisé</h2>
            <p className="mt-1 text-xs text-slate-500">
              Connexion navigateur, réutilisation des cookies et acquisition en lot.
            </p>
          </div>
          <Badge tone={access.enabled ? "warning" : "neutral"}>
            {access.enabled ? "Test activé" : "Désactivé dans config.yaml"}
          </Badge>
        </div>
      </CardHeader>
      <CardBody className="grid gap-6 xl:grid-cols-2">
        <form className="space-y-4" onSubmit={onSaveCredentials}>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-bold text-slate-800">Identifiants LDAP</h3>
            <Badge tone={access.credentials_configured ? "success" : "warning"}>
              {access.credentials_configured ? "Conservés" : "Absents"}
            </Badge>
          </div>
          <Field label="Identifiant LDAP">
            <Input
              autoComplete="username"
              disabled={!access.enabled}
              name="publisher_username"
              required
            />
          </Field>
          <Field hint="Chiffré avec Windows DPAPI, jamais renvoyé par l’API" label="Mot de passe">
            <Input
              autoComplete="current-password"
              disabled={!access.enabled}
              name="publisher_password"
              required
              type="password"
            />
          </Field>
          <div className="flex flex-wrap gap-3">
            <Button
              disabled={!access.enabled}
              loading={busy === "publisher-credentials"}
              type="submit"
            >
              <KeyRound aria-hidden="true" className="size-4" />
              Conserver
            </Button>
            <Button
              disabled={!access.credentials_configured}
              loading={busy === "publisher-delete"}
              onClick={onDeleteCredentials}
              type="button"
              variant="danger"
            >
              <Trash2 aria-hidden="true" className="size-4" />
              Supprimer
            </Button>
          </div>
        </form>
        <form className="space-y-4" onSubmit={onStartRun}>
          <h3 className="text-sm font-bold text-slate-800">Acquisition en lot</h3>
          <Field label="Profil publisher">
            <Select disabled={!access.enabled} name="publisher_profile" required>
              {access.profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            hint={`DOI ou identifiants de notices, maximum ${access.max_records_per_run}`}
            label="Cibles, une par ligne"
          >
            <Textarea disabled={!access.enabled} name="publisher_targets" required />
          </Field>
          <Field label="Référence de l’autorisation explicite">
            <Input
              disabled={!access.enabled}
              name="authorization_reference"
              placeholder="Courriel, ticket ou convention"
              required
            />
          </Field>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              disabled={!access.enabled || !access.credentials_configured}
              loading={busy === "publisher-run"}
              type="submit"
            >
              <ScanSearch aria-hidden="true" className="size-4" />
              Lancer la collecte
            </Button>
            {runId && (
              <Button
                loading={busy === "publisher-refresh"}
                onClick={onRefreshRun}
                type="button"
                variant="secondary"
              >
                <RefreshCw aria-hidden="true" className="size-4" />
                {runState ?? "Actualiser"}
              </Button>
            )}
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
