import { useCallback, useState } from "react";

import { PackageCheck, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import type { ExpertDistributionState } from "@/types/api";

const stateLabels: Record<ExpertDistributionState, string> = {
  imported: "Importé",
  proposed: "Proposé",
  approved: "Approuvé",
  rejected: "Rejeté",
  activated: "Activé",
  rolled_back: "Annulé",
};

const stateTones: Record<ExpertDistributionState, "neutral" | "warning" | "success" | "danger"> = {
  imported: "neutral",
  proposed: "warning",
  approved: "success",
  rejected: "danger",
  activated: "success",
  rolled_back: "warning",
};

export function ExpertDistributionPage() {
  // Activation and rollback use the displayed generation/release values as CAS guards.
  const loadDistributions = useCallback(() => api.expertMemory.distributions(), []);
  const { data, error, loading, refresh } = useRemoteData(loadDistributions);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [generation, setGeneration] = useState("0");
  const [activeRelease, setActiveRelease] = useState("");
  const [targetRelease, setTargetRelease] = useState("");
  const [targetHash, setTargetHash] = useState("");
  const [rollbackGeneration, setRollbackGeneration] = useState("1");
  const [rollbackActiveRelease, setRollbackActiveRelease] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const selected = data?.distributions.find((item) => item.id === selectedId) ?? null;

  const run = async (operation: () => Promise<unknown>, pendingKey?: string) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      await operation();
      if (pendingKey) forgetPendingRequest(pendingKey);
      await refresh();
    } catch (caught: unknown) {
      await refresh();
      setErrorMessage(caught instanceof Error ? caught.message : "L’opération a échoué.");
    } finally {
      setBusy(false);
    }
  };

  if (loading && !data) return <LoadingState label="Lecture des distributions locales…" />;
  if (error && !data) return <ErrorState message={error} retry={refresh} />;
  if (!data) return null;

  return (
    <div className="space-y-8">
      <PageHeader
        actions={
          <Button loading={loading} onClick={refresh} variant="secondary">
            <RefreshCw aria-hidden="true" className="size-4" />
            Actualiser
          </Button>
        }
        description="Gérez les paquets importés localement. Chaque transition reste explicite, vérifiable et rejouable."
        eyebrow="Mémoire experte"
        title="Distribution locale"
      />

      {error && <ErrorState message={error} retry={refresh} />}
      {errorMessage && (
        <p
          className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"
          role="alert"
        >
          {errorMessage}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <Card>
          <CardHeader>
            <p className="font-bold text-slate-900">Paquets locaux</p>
            <p className="mt-1 text-sm text-slate-500">
              Les métadonnées sont affichées, jamais le contenu intégral.
            </p>
          </CardHeader>
          <CardBody>
            {data.distributions.length === 0 ? (
              <p className="text-sm text-slate-500">Aucun paquet importé.</p>
            ) : (
              <ul aria-label="Distributions locales" className="space-y-2">
                {data.distributions.map((distribution) => (
                  <li key={distribution.id}>
                    <button
                      className={`w-full rounded-xl border px-3 py-3 text-left transition ${
                        distribution.id === selectedId
                          ? "border-forest-500 bg-forest-50"
                          : "border-slate-200 hover:border-forest-300 hover:bg-slate-50"
                      }`}
                      onClick={() => {
                        setSelectedId(distribution.id);
                        setErrorMessage(null);
                      }}
                      type="button"
                    >
                      <span className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-semibold text-slate-800">
                          {distribution.release_id}
                        </span>
                        <Badge tone={stateTones[distribution.state]}>
                          {stateLabels[distribution.state]}
                        </Badge>
                      </span>
                      <span className="mt-1 block truncate text-xs text-slate-500">
                        {distribution.package_sha256}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <PackageCheck aria-hidden="true" className="size-5 text-forest-600" />
              <div>
                <p className="font-bold text-slate-900">Contrôle de transition</p>
                <p className="mt-1 text-sm text-slate-500">
                  Les générations attendues protègent contre une activation obsolète.
                </p>
              </div>
            </div>
          </CardHeader>
          <CardBody>
            {!selected ? (
              <p className="text-sm text-slate-500">
                Sélectionnez un paquet pour afficher ses actions.
              </p>
            ) : (
              <div className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-2">
                  <HashValue label="Release" value={selected.release_id} />
                  <HashValue label="Hash paquet" value={selected.package_sha256} />
                </div>

                {selected.state === "imported" && (
                  <Button
                    loading={busy}
                    onClick={() => run(() => api.expertMemory.proposeDistribution(selected.id))}
                  >
                    Proposer le paquet
                  </Button>
                )}

                {selected.state === "proposed" && (
                  <div className="space-y-4">
                    <TextField
                      id="distribution-reviewer"
                      label="Identifiant du relecteur"
                      onChange={setReviewer}
                      value={reviewer}
                    />
                    <TextAreaField
                      id="distribution-reason"
                      label="Justification"
                      onChange={setReason}
                      value={reason}
                    />
                    <Button
                      loading={busy}
                      onClick={() =>
                        run(() =>
                          api.expertMemory.approveDistribution(selected.id, {
                            reviewer_label: reviewer.trim(),
                            reason: reason.trim(),
                          }),
                        )
                      }
                    >
                      Approuver localement
                    </Button>
                  </div>
                )}

                {selected.state === "approved" && (
                  <div className="space-y-4">
                    <p
                      className="flex items-start gap-2 text-sm leading-6 text-emerald-800"
                      role="status"
                    >
                      <ShieldCheck aria-hidden="true" className="mt-1 size-4 shrink-0" />
                      Le paquet est approuvé. Renseignez l’état actif observé avant le CAS.
                    </p>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <TextField
                        id="activation-generation"
                        label="Génération active attendue"
                        onChange={setGeneration}
                        type="number"
                        value={generation}
                      />
                      <TextField
                        id="activation-release"
                        label="Release active attendue (optionnel)"
                        onChange={setActiveRelease}
                        value={activeRelease}
                      />
                    </div>
                    <Button
                      loading={busy}
                      onClick={() =>
                        run(
                          () =>
                            api.expertMemory.activateDistribution(selected.id, {
                              client_request_id: pendingRequest(
                                distributionRequestKey("activation", selected.id),
                              ),
                              expected_active_generation: Number(generation),
                              expected_active_release_id: activeRelease.trim() || null,
                            }),
                          distributionRequestKey("activation", selected.id),
                        )
                      }
                    >
                      Activer explicitement
                    </Button>
                  </div>
                )}

                {selected.state === "activated" && (
                  <div className="space-y-4">
                    <p className="text-sm leading-6 text-slate-600">
                      Un rollback exige la release parente et son hash exact.
                    </p>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <TextField
                        id="rollback-generation"
                        label="Génération active attendue"
                        onChange={setRollbackGeneration}
                        type="number"
                        value={rollbackGeneration}
                      />
                      <TextField
                        id="rollback-active-release"
                        label="Release active attendue"
                        onChange={setRollbackActiveRelease}
                        value={rollbackActiveRelease}
                      />
                      <TextField
                        id="rollback-target-release"
                        label="Release cible"
                        onChange={setTargetRelease}
                        value={targetRelease}
                      />
                      <TextField
                        id="rollback-target-hash"
                        label="Hash de la release cible"
                        onChange={setTargetHash}
                        value={targetHash}
                      />
                    </div>
                    <TextAreaField
                      id="rollback-reason"
                      label="Justification du rollback"
                      onChange={setReason}
                      value={reason}
                    />
                    <Button
                      loading={busy}
                      onClick={() =>
                        run(
                          () =>
                            api.expertMemory.rollbackDistribution(selected.id, {
                              client_request_id: pendingRequest(
                                distributionRequestKey("rollback", selected.id),
                              ),
                              target_release_id: targetRelease.trim(),
                              target_release_sha256: targetHash.trim(),
                              expected_active_generation: Number(rollbackGeneration),
                              expected_active_release_id: rollbackActiveRelease.trim(),
                              reason: reason.trim(),
                            }),
                          distributionRequestKey("rollback", selected.id),
                        )
                      }
                      variant="danger"
                    >
                      <RotateCcw aria-hidden="true" className="size-4" />
                      Exécuter le rollback
                    </Button>
                  </div>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function distributionRequestKey(action: "activation" | "rollback", distributionId: string) {
  return `ciderscholar.expert-distribution.${action}.${distributionId}`;
}

function pendingRequest(key: string): string {
  try {
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    const requestId = crypto.randomUUID();
    window.localStorage.setItem(key, requestId);
    return requestId;
  } catch {
    return crypto.randomUUID();
  }
}

function forgetPendingRequest(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in a locked-down browser; the API remains safe.
  }
}

function HashValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-slate-50 p-3">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 truncate font-mono text-xs text-slate-800" title={value}>
        {value}
      </p>
    </div>
  );
}

function TextField({
  id,
  label,
  onChange,
  type = "text",
  value,
}: {
  id: string;
  label: string;
  onChange: (value: string) => void;
  type?: "number" | "text";
  value: string;
}) {
  return (
    <label className="text-sm font-semibold text-slate-800" htmlFor={id}>
      {label}
      <input
        className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 px-3 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
        id={id}
        min={type === "number" ? 0 : undefined}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        value={value}
      />
    </label>
  );
}

function TextAreaField({
  id,
  label,
  onChange,
  value,
}: {
  id: string;
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className="block text-sm font-semibold text-slate-800" htmlFor={id}>
      {label}
      <textarea
        className="mt-2 min-h-24 w-full rounded-[10px] border border-slate-300 px-3 py-2 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
        id={id}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}
