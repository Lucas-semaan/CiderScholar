import { Download, HardDrive, Power, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";
import type { RuntimeSettings } from "@/types/api";

interface SettingsStatusCardsProps {
  busy: string | null;
  onCorpusAction: (
    action: string,
    confirmation: string,
    operation: () => Promise<{ message: string }>,
  ) => void;
  onShutdown: () => void;
  settings: RuntimeSettings;
}

function SecurityLine({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span>{label}</span>
      <Badge tone={active ? "success" : "warning"}>{active ? "Actif" : "À vérifier"}</Badge>
    </div>
  );
}

export function SettingsStatusCards({
  busy,
  onCorpusAction,
  onShutdown,
  settings,
}: SettingsStatusCardsProps) {
  const corpus = settings.corpus_update;
  const application = settings.application_update;
  const corpusActions = [
    {
      action: "corpus-download",
      confirmation:
        "Télécharger et vérifier la nouvelle version sans l’installer ? Le corpus actif restera inchangé.",
      label: "Télécharger",
      loading: "corpus-download",
      operation: api.corpusUpdates.download,
      secondary: true,
      disabled: !corpus.download_required,
    },
    {
      action: "corpus-install",
      confirmation:
        "Installer la version téléchargée au prochain redémarrage ? Le corpus actuel restera actif jusque-là.",
      label: "Installer au redémarrage",
      loading: "corpus-install",
      operation: api.corpusUpdates.installOnRestart,
    },
  ];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-slate-900">Version du corpus commun</h2>
              <p className="mt-1 text-xs text-slate-500">{corpus.message}</p>
            </div>
            <Badge tone={corpus.update_available ? "warning" : "success"}>
              {corpus.update_available ? "Disponible" : "À jour"}
            </Badge>
          </div>
        </CardHeader>
        <CardBody className="space-y-3 text-xs text-slate-600">
          <p className="break-all">
            <strong className="text-slate-900">Installée :</strong>{" "}
            {corpus.installed_version ?? "non renseignée"}
          </p>
          <p className="break-all">
            <strong className="text-slate-900">Disponible :</strong>{" "}
            {corpus.available_version ?? "non synchronisée"}
          </p>
          <p>
            <strong className="text-slate-900">Publication :</strong>{" "}
            {corpus.published_at ? new Date(corpus.published_at).toLocaleString("fr-FR") : "—"}
          </p>
          <div className="grid gap-2 pt-2 sm:grid-cols-2">
            {corpusActions.map(
              ({ action, confirmation, label, loading, operation, secondary, disabled }) => (
                <Button
                  disabled={disabled}
                  key={action}
                  loading={busy === loading}
                  onClick={() => onCorpusAction(action, confirmation, operation)}
                  variant={secondary ? "secondary" : "primary"}
                >
                  {secondary ? (
                    <Download aria-hidden="true" className="size-4" />
                  ) : (
                    <RefreshCw aria-hidden="true" className="size-4" />
                  )}
                  {label}
                </Button>
              ),
            )}
            <Button
              className="sm:col-span-2"
              loading={busy === "corpus-rollback"}
              onClick={() =>
                onCorpusAction(
                  "corpus-rollback",
                  "Revenir à la version précédente au prochain redémarrage ? La version actuelle restera active jusque-là.",
                  api.corpusUpdates.rollbackOnRestart,
                )
              }
              variant="danger"
            >
              <RotateCcw aria-hidden="true" className="size-4" />
              Revenir à la version précédente
            </Button>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-slate-900">Version de l’application</h2>
              <p className="mt-1 text-xs text-slate-500">
                Vérification distincte du corpus, sans installation automatique.
              </p>
            </div>
            <Badge
              tone={
                application.state === "available"
                  ? "warning"
                  : application.state === "invalid"
                    ? "danger"
                    : "success"
              }
            >
              {application.state === "available" ? "Disponible" : "Contrôlée"}
            </Badge>
          </div>
        </CardHeader>
        <CardBody className="space-y-2 text-xs leading-5 text-slate-600">
          <p>{application.message}</p>
          <p>
            <strong className="text-slate-900">Installée :</strong> {application.installed_version}{" "}
            · <strong className="text-slate-900">Publiée :</strong>{" "}
            {application.available_version ?? "—"}
          </p>
          {application.active_jobs > 0 && (
            <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800">
              {application.active_jobs} travail(s) actif(s) : remplacement reporté.
            </p>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Power aria-hidden="true" className="size-5 text-red-700" />
            <h2 className="font-bold text-slate-900">Arrêt de l’application</h2>
          </div>
        </CardHeader>
        <CardBody className="space-y-3 text-sm leading-6 text-slate-600">
          <p>
            Le worker termine sa frontière sûre et conserve la file avant la fermeture de l’API
            locale.
          </p>
          <Button loading={busy === "shutdown"} onClick={onShutdown} variant="danger">
            <Power aria-hidden="true" className="size-4" />
            Arrêter CiderScholar
          </Button>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="size-5 text-forest-600" />
            <h2 className="font-bold text-slate-900">Périmètre de sécurité</h2>
          </div>
        </CardHeader>
        <CardBody className="space-y-4 text-sm leading-6 text-slate-600">
          <SecurityLine active label="Interface liée à 127.0.0.1" />
          <SecurityLine active label="SQLite et Qdrant restent locaux" />
          <SecurityLine
            active={settings.llm_key_configured}
            label="Clé ARGO chiffrée avec Windows DPAPI"
          />
          <SecurityLine
            active={settings.harvest.free_openalex_only}
            label="OpenAlex limité au mode gratuit"
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <HardDrive aria-hidden="true" className="size-5 text-cider-500" />
            <h2 className="font-bold text-slate-900">Stockage</h2>
          </div>
        </CardHeader>
        <CardBody>
          <p className="break-all font-mono text-xs leading-6 text-slate-500">
            {settings.data_directory}
          </p>
          <Badge className="mt-4" tone={settings.offline_mode ? "neutral" : "success"}>
            {settings.offline_mode ? "Configuration invalide" : "ARGO en ligne"}
          </Badge>
        </CardBody>
      </Card>
    </div>
  );
}
