import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { Field, Input, Textarea } from "@/components/ui/Form";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import type { WatchConfiguration, WatchReport } from "@/types/api";

const dateLabel = (value: string) => new Date(value).toLocaleString("fr-FR");

export function WatchForm({
  configuration,
  save,
  busy,
}: {
  configuration: WatchConfiguration;
  save: (config: WatchConfiguration) => Promise<void>;
  busy: boolean;
}) {
  const [config, setConfig] = useState(configuration);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void save(config);
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <label className="flex min-h-11 items-center gap-3 text-sm font-medium">
        <input
          type="checkbox"
          checked={config.enabled}
          className="size-5 accent-sky-600 focus-visible:outline-2 focus-visible:outline-sky-600"
          onChange={(event) =>
            setConfig((previous) => ({ ...previous, enabled: event.target.checked }))
          }
        />
        Activer la veille hebdomadaire
      </label>
      <p className="text-sm text-slate-600">
        Un premier scan après activation, puis tous les sept jours. Une échéance manquée est
        rattrapée à la réouverture. Environ 1 h, 1 000 notices et 100 acquisitions au maximum par
        scan.
      </p>
      <fieldset className="space-y-3">
        <legend className="mb-2 text-sm font-semibold">Thèmes et mots-clés de recherche</legend>
        {config.themes.map((theme, index) => (
          <div
            key={index}
            className="grid gap-2 rounded-lg border border-slate-200 p-3 sm:grid-cols-[1fr_3fr_auto]"
          >
            <Field label="Tag">
              <Input
                required
                pattern="[a-z][a-z0-9_-]{1,63}"
                maxLength={64}
                value={theme.key}
                onChange={(event) =>
                  setConfig((previous) => ({
                    ...previous,
                    themes: previous.themes.map((item, position) =>
                      position === index ? { ...item, key: event.target.value } : item,
                    ),
                  }))
                }
              />
            </Field>
            <Field label="Mots-clés">
              <Textarea
                required
                minLength={2}
                maxLength={500}
                value={theme.query}
                onChange={(event) =>
                  setConfig((previous) => ({
                    ...previous,
                    themes: previous.themes.map((item, position) =>
                      position === index ? { ...item, query: event.target.value } : item,
                    ),
                  }))
                }
              />
            </Field>
            <Button
              variant="secondary"
              type="button"
              disabled={config.themes.length === 1}
              aria-label={`Retirer le thème ${theme.key}`}
              onClick={() =>
                setConfig((previous) => ({
                  ...previous,
                  themes: previous.themes.filter((_, position) => position !== index),
                }))
              }
            >
              Retirer
            </Button>
          </div>
        ))}
      </fieldset>
      <div className="flex flex-wrap gap-3">
        <Button
          type="button"
          variant="secondary"
          disabled={config.themes.length >= 32}
          onClick={() =>
            setConfig((previous) => ({
              ...previous,
              themes: [...previous.themes, { key: "", query: "" }],
            }))
          }
        >
          Ajouter un thème
        </Button>
        <Button type="submit" loading={busy}>
          Enregistrer la veille
        </Button>
      </div>
    </form>
  );
}

export function WatchSummary({ report }: { report: WatchReport }) {
  return (
    <div className="space-y-2 rounded-lg bg-slate-50 p-4 text-sm">
      <p>
        {dateLabel(report.started_at)} ·{" "}
        {
          {
            running: "En cours",
            completed: "Terminé",
            partial: "Partiel",
            failed: "Échec",
            cancelled: "Annulé",
          }[report.state]
        }
      </p>
      <p>
        <strong>{report.added_to_rag} ajout(s) au RAG</strong> : {report.full_articles} texte(s)
        intégral(aux), {report.abstracts_only} abstract(s) seul(s).
      </p>
      <p>
        {report.examined} notices examinées · {report.duplicates} doublons · {report.accepted}{" "}
        admises · {report.review} à examiner · {report.rejected} rejetées · {report.deferred}{" "}
        différées.
      </p>
      {(report.errors.length > 0 || report.limitations.length > 0) && (
        <details>
          <summary className="min-h-11 cursor-pointer py-2 focus-visible:outline-2 focus-visible:outline-sky-600">
            Erreurs et limites ({report.errors.length + report.limitations.length})
          </summary>
          <ul className="list-inside list-disc break-words">
            {[...report.errors, ...report.limitations].map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function BibliographicWatchCard() {
  const load = useCallback(() => api.bibliographicWatch.status(), []);
  const remote = useRemoteData(load);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setInterval(remote.refresh, 15000);
    return () => window.clearInterval(timer);
  }, [remote.refresh]);
  const act = async (operation: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await operation();
      setFeedback(message);
      remote.refresh();
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Opération impossible.");
    } finally {
      setBusy(false);
    }
  };
  if (!remote.data && remote.loading) return <LoadingState label="Lecture de la veille…" />;
  if (!remote.data)
    return <ErrorState message={remote.error ?? "Veille indisponible."} retry={remote.refresh} />;
  const { configuration, next_due_at: due, active_job: active, history } = remote.data;
  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Veille bibliographique</h2>
        <Badge tone={history[0]?.added_to_rag ? "success" : "neutral"}>
          {active
            ? "Scan en cours ou en attente"
            : history[0]?.added_to_rag
              ? `${history[0].added_to_rag} nouveaux articles au dernier scan`
              : configuration.enabled
                ? "Activée"
                : "Désactivée"}
        </Badge>
      </CardHeader>
      <CardBody className="space-y-5">
        {remote.data.suspended_reason && (
          <p role="status" className="text-sm text-amber-800">
            {remote.data.suspended_reason}
          </p>
        )}
        <WatchForm
          key={JSON.stringify(configuration)}
          configuration={configuration}
          busy={busy}
          save={(config) =>
            act(() => api.bibliographicWatch.configure(config), "Réglages enregistrés.")
          }
        />
        <p className="text-sm text-slate-600">
          {configuration.enabled
            ? due
              ? `Prochaine échéance : ${dateLabel(due)}`
              : "Premier scan à venir."
            : "Aucun scan automatique programmé."}
        </p>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="secondary"
            loading={busy}
            disabled={!!active}
            onClick={() => void act(api.bibliographicWatch.launch, "Scan mis en file.")}
          >
            Lancer maintenant
          </Button>
          {active && active.state !== "cancel_requested" && (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void act(
                  () => api.jobs.cancel(active.id),
                  "Annulation demandée ; les ajouts persistés sont conservés.",
                )
              }
            >
              Annuler le scan
            </Button>
          )}
        </div>
        {(error || remote.error) && (
          <p role="alert" className="text-sm text-red-700">
            {error || remote.error}
          </p>
        )}
        {feedback && (
          <p role="status" className="text-sm text-sky-800">
            {feedback}
          </p>
        )}
        {history.length ? (
          <details open>
            <summary className="min-h-11 cursor-pointer py-2 font-medium focus-visible:outline-2 focus-visible:outline-sky-600">
              Historique des scans
            </summary>
            <div className="space-y-3">
              {history.map((report) => (
                <WatchSummary key={report.job_id} report={report} />
              ))}
            </div>
          </details>
        ) : (
          <p className="text-sm text-slate-600">Aucun scan exécuté.</p>
        )}
      </CardBody>
    </Card>
  );
}
