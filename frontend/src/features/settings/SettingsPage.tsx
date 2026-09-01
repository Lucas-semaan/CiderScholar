import { useCallback, useState, type FormEvent } from "react";

import { Network } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { AdminMaintenanceCard } from "@/features/settings/AdminMaintenanceCard";
import { LlmProviderSettingsCards } from "@/features/settings/LlmProviderSettingsCards";
import { PublisherAccessCard } from "@/features/settings/PublisherAccessCard";
import { RuntimeSummary } from "@/features/settings/RuntimeSummary";
import { SessionSettingsCard } from "@/features/settings/SessionSettingsCard";
import { SettingsFeedback } from "@/features/settings/SettingsFeedback";
import { SettingsStatusCards } from "@/features/settings/SettingsStatusCards";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import type { LlmProviderId } from "@/types/api";

const errorMessage = (caught: unknown, fallback: string) =>
  caught instanceof Error ? caught.message : fallback;

export function SettingsPage() {
  const loadSettings = useCallback(() => api.system.settings(), []);
  const loadProviders = useCallback(() => api.llmProviders.list(), []);
  const runtime = useRemoteData(loadSettings);
  const providers = useRemoteData(loadProviders);
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

  if ((runtime.loading && !runtime.data) || (providers.loading && !providers.data))
    return <LoadingState label="Lecture de la configuration…" />;
  if (runtime.error && !runtime.data)
    return <ErrorState message={runtime.error} retry={runtime.refresh} />;
  if (providers.error && !providers.data)
    return <ErrorState message={providers.error} retry={providers.refresh} />;
  if (!runtime.data || !providers.data) return null;

  const refreshSettings = () => {
    runtime.refresh();
    providers.refresh();
  };
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
  const saveProvider = (provider: LlmProviderId, event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const key = String(values.get("key") ?? "").trim();
    const baseUrl = String(values.get("base_url") ?? "").trim();
    const model = String(values.get("model") ?? "").trim();
    setMessage(null);
    void runAction(
      `llm-provider-${provider}-save`,
      () =>
        api.llmProviders.save(provider, {
          ...(key ? { key } : {}),
          ...(provider === "custom" ? { base_url: baseUrl, model } : {}),
        }),
      () => {
        form.reset();
        setMessage(
          `${provider === "argo" ? "Clé ARGO INRAE" : "Fournisseur personnalisé"} enregistré. Vous pouvez maintenant tester la connexion.`,
        );
        refreshSettings();
      },
      "La configuration du fournisseur n’a pas pu être enregistrée.",
    );
  };
  const testProvider = (provider: LlmProviderId) => {
    void runAction(
      `llm-provider-${provider}-test`,
      () => api.llmProviders.test(provider),
      (result) =>
        result.state === "ready" ? setMessage(result.message) : setError(result.message),
      "Le test du fournisseur a échoué.",
    );
  };
  const deleteProvider = (provider: LlmProviderId) => {
    void runAction(
      `llm-provider-${provider}-delete`,
      () => api.llmProviders.remove(provider),
      () => {
        setMessage(
          `Clé ${provider === "argo" ? "ARGO INRAE" : "du fournisseur personnalisé"} supprimée.`,
        );
        refreshSettings();
      },
      "La clé du fournisseur n’a pas pu être supprimée.",
    );
  };
  const activateProvider = (provider: LlmProviderId) => {
    void runAction(
      "llm-provider-activate",
      () => api.llmProviders.activate(provider),
      () => {
        setMessage(
          `${provider === "argo" ? "ARGO INRAE" : "Le fournisseur personnalisé"} est maintenant actif.`,
        );
        refreshSettings();
      },
      "Le fournisseur n’a pas pu être sélectionné.",
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
  const providerSettings = providers.data;
  const activeProvider = providerSettings.providers.find(
    (provider) => provider.id === providerSettings.active_provider,
  );
  return (
    <div className="space-y-8">
      <PageHeader
        description="Choisissez votre fournisseur LLM et contrôlez les paramètres de session sans exposer de clé."
        eyebrow="Exploitation locale"
        title="Paramètres"
        actions={
          <Button loading={busy === "health"} onClick={probe} variant="secondary">
            <Network aria-hidden="true" className="size-4" />
            Tester le LLM
          </Button>
        }
      />
      <RuntimeSummary provider={activeProvider} settings={settings} />
      <SettingsFeedback
        error={error}
        health={health}
        message={message}
        modelName={settings.llm_model}
      />
      {settings.administrator && <AdminMaintenanceCard />}
      <LlmProviderSettingsCards
        busy={busy}
        onActivate={activateProvider}
        onDelete={deleteProvider}
        onSave={saveProvider}
        onTest={testProvider}
        providers={providerSettings.providers}
      />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,42rem)_minmax(18rem,1fr)]">
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
