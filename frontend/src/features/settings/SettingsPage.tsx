import { useCallback, useState, type FormEvent } from "react";

import { Network } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { AdminMaintenanceCard } from "@/features/settings/AdminMaintenanceCard";
import { ArgoKeySettingsCard } from "@/features/settings/ArgoKeySettingsCard";
import { PublisherAccessCard } from "@/features/settings/PublisherAccessCard";
import { RuntimeSummary } from "@/features/settings/RuntimeSummary";
import { SessionSettingsCard } from "@/features/settings/SessionSettingsCard";
import { SettingsFeedback } from "@/features/settings/SettingsFeedback";
import { SettingsStatusCards } from "@/features/settings/SettingsStatusCards";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";

const errorMessage = (caught: unknown, fallback: string) =>
  caught instanceof Error ? caught.message : fallback;

export function SettingsPage() {
  const loadSettings = useCallback(() => api.system.settings(), []);
  const runtime = useRemoteData(loadSettings);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [publisherRunId, setPublisherRunId] = useState<string | null>(null);
  const [publisherRunState, setPublisherRunState] = useState<string | null>(null);

  const runAction = async <Result,>(
    action: string,
    operation: () => Promise<Result>,
    onSuccess?: (result: Result) => void,
    fallback = "Erreur inconnue",
  ) => {
    setBusy(action);
    setError(null);
    try {
      const result = await operation();
      onSuccess?.(result);
    } catch (caught: unknown) {
      setError(errorMessage(caught, fallback));
    } finally {
      setBusy(null);
    }
  };

  if (runtime.loading && !runtime.data)
    return <LoadingState label="Lecture de la configuration…" />;
  if (runtime.error && !runtime.data)
    return <ErrorState message={runtime.error} retry={runtime.refresh} />;
  if (!runtime.data) return null;

  const refreshSettings = () => void runtime.refresh();
  const showMessageAndRefresh = (result: { message: string }) => {
    setMessage(result.message);
    refreshSettings();
  };
  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setMessage(null);
    void runAction(
      "save",
      () =>
        api.system.updateSettings({
          default_article_count: Number(values.get("default_article_count")),
          lexical_weight: Number(values.get("lexical_weight")),
          vector_weight: Number(values.get("vector_weight")),
          reranker_weight: Number(values.get("reranker_weight")),
          embedding_batch_size: Number(values.get("embedding_batch_size")),
          passages_per_article: Number(values.get("passages_per_article")),
        }),
      () => {
        setMessage(
          "Configuration appliquée à cette session. Le fichier config.yaml reste inchangé.",
        );
        refreshSettings();
      },
    );
  };
  const probe = () => {
    void runAction("health", api.system.llmHealth, setHealth, "Moteur indisponible");
  };
  const shutdown = () => {
    if (!window.confirm("Arrêter CiderScholar après persistance du travail actif ?")) return;
    void runAction(
      "shutdown",
      api.system.shutdown,
      (result) => setMessage(result.message),
      "L’arrêt n’a pas pu être demandé.",
    );
  };
  const replaceArgoKey = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const key = String(new FormData(form).get("argo_key") ?? "");
    setMessage(null);
    void runAction(
      "argo-key-save",
      async () => {
        await api.argoKey.save(key);
        return api.argoKey.test();
      },
      (result) => {
        if (result.state !== "ready") {
          setError(result.message);
          return;
        }
        form.reset();
        setMessage("Clé ARGO vérifiée et chiffrée pour ce compte Windows.");
        refreshSettings();
      },
      "La clé n’a pas pu être remplacée.",
    );
  };
  const testArgoKey = () => {
    void runAction(
      "argo-key-test",
      api.argoKey.test,
      (result) =>
        result.state === "ready" ? setMessage(result.message) : setError(result.message),
      "Le test ARGO a échoué.",
    );
  };
  const deleteArgoKey = () => {
    void runAction(
      "argo-key-delete",
      api.argoKey.remove,
      () => {
        setMessage("Clé ARGO supprimée de ce compte Windows.");
        refreshSettings();
      },
      "La clé n’a pas pu être supprimée.",
    );
  };
  const savePublisherCredentials = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    void runAction(
      "publisher-credentials",
      () =>
        api.publisherAccess.saveCredentials({
          username: String(values.get("publisher_username") ?? ""),
          password: String(values.get("publisher_password") ?? ""),
          authorization_confirmed: true,
        }),
      () => {
        form.reset();
        setMessage("Identifiants LDAP protégés par DPAPI pour cet utilisateur Windows.");
        refreshSettings();
      },
    );
  };
  const deletePublisherCredentials = () => {
    void runAction("publisher-delete", api.publisherAccess.deleteCredentials, () => {
      setMessage("Identifiants LDAP supprimés du profil Windows.");
      refreshSettings();
    });
  };
  const startPublisherRun = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const targets = String(values.get("publisher_targets") ?? "")
      .split(/[\s,;]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    void runAction(
      "publisher-run",
      () =>
        api.publisherAccess.startRun({
          profile_id: String(values.get("publisher_profile") ?? ""),
          targets,
          authorization_reference: String(values.get("authorization_reference") ?? ""),
          authorization_confirmed: true,
        }),
      (started) => {
        setPublisherRunId(started.run_id);
        setPublisherRunState(started.state);
        setMessage(`Collecte ${started.run_id} lancée pour ${started.target_count} notice(s).`);
      },
    );
  };
  const refreshPublisherRun = () => {
    if (!publisherRunId) return;
    void runAction(
      "publisher-refresh",
      () => api.publisherAccess.run(publisherRunId),
      (run) => setPublisherRunState(run.state),
    );
  };
  const confirmCorpusAction = (
    action: string,
    confirmation: string,
    operation: () => Promise<{ message: string }>,
  ) => {
    if (!window.confirm(confirmation)) return;
    setMessage(null);
    void runAction(action, operation, showMessageAndRefresh, "Action impossible sur le corpus.");
  };

  const settings = runtime.data;
  return (
    <div className="space-y-8">
      <PageHeader
        description="Contrôlez ARGO et les paramètres de session sans exposer de clé ni réécrire la configuration locale."
        eyebrow="Exploitation locale"
        title="Paramètres"
        actions={
          <Button loading={busy === "health"} onClick={probe} variant="secondary">
            <Network aria-hidden="true" className="size-4" />
            Tester le LLM
          </Button>
        }
      />
      <RuntimeSummary settings={settings} />
      <SettingsFeedback
        error={error}
        health={health}
        message={message}
        modelName={settings.llm_model}
      />
      {settings.administrator && <AdminMaintenanceCard />}
      <ArgoKeySettingsCard
        busy={busy}
        configured={settings.llm_key_configured}
        onDelete={deleteArgoKey}
        onSave={replaceArgoKey}
        onTest={testArgoKey}
      />
      <div className="grid gap-6 xl:grid-cols-[1.2fr_.8fr]">
        <SessionSettingsCard busy={busy === "save"} onSave={save} settings={settings} />
        <SettingsStatusCards
          busy={busy}
          onCorpusAction={confirmCorpusAction}
          onShutdown={shutdown}
          settings={settings}
        />
      </div>
      <PublisherAccessCard
        busy={busy}
        onDeleteCredentials={deletePublisherCredentials}
        onRefreshRun={refreshPublisherRun}
        onSaveCredentials={savePublisherCredentials}
        onStartRun={startPublisherRun}
        runId={publisherRunId}
        runState={publisherRunState}
        settings={settings}
      />
    </div>
  );
}
