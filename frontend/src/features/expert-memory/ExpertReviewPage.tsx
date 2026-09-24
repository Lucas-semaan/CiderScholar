import { useCallback, useState } from "react";

import { CheckCircle2, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { PilotObservationForm } from "@/features/expert-memory/PilotObservationForm";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import type {
  ExpertCandidateSummary,
  ExpertPilotResponse,
  ExpertReviewResponse,
} from "@/types/api";

const stateLabels: Record<ExpertCandidateSummary["state"], string> = {
  draft: "Brouillon",
  structurally_valid: "Structure valide",
  evaluating: "En évaluation",
  awaiting_review: "À revoir",
  approved: "Approuvé",
  activated: "Activé",
  needs_expert: "Expert requis",
  evaluation_failed: "Évaluation échouée",
  rejected: "Rejeté",
  superseded: "Remplacé",
  inconclusive: "Inconclusif",
};

export function ExpertReviewPage() {
  const loadCandidates = useCallback(() => api.expertMemory.candidates(), []);
  const { data, error, loading, refresh } = useRemoteData(loadCandidates);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [review, setReview] = useState<ExpertReviewResponse | null>(null);
  const [pilot, setPilot] = useState<ExpertPilotResponse | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [pilotReference, setPilotReference] = useState("");
  const [decision, setDecision] = useState<"approve" | "reject" | "needs_changes">("approve");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const selected = data?.candidates.find((candidate) => candidate.id === selectedId) ?? null;
  const canReview =
    selected?.state === "awaiting_review" &&
    selected.evaluation_state === "passed" &&
    selected.evaluation_sha256 !== null;

  const submitReview = async () => {
    if (!selected || !canReview || !selected.evaluation_sha256) return;
    if (!reviewer.trim() || !reason.trim()) {
      setErrorMessage("Indiquez votre identifiant de revue et une justification.");
      return;
    }
    setErrorMessage(null);
    setSubmitting(true);
    try {
      const created = await api.expertMemory.reviewCandidate(selected.id, {
        client_request_id: crypto.randomUUID(),
        candidate_sha256: selected.candidate_sha256,
        evaluation_sha256: selected.evaluation_sha256,
        decision,
        reviewer_label: reviewer.trim(),
        reason: reason.trim(),
      });
      setReview(created);
      await refresh();
    } catch (caught: unknown) {
      setErrorMessage(caught instanceof Error ? caught.message : "La revue a échoué.");
    } finally {
      setSubmitting(false);
    }
  };

  const activate = async () => {
    if (!selected || !review || !selected.evaluation_sha256) return;
    setErrorMessage(null);
    setSubmitting(true);
    try {
      await api.expertMemory.activateCandidate(selected.id, {
        client_request_id: crypto.randomUUID(),
        review_id: review.id,
        candidate_sha256: selected.candidate_sha256,
        evaluation_sha256: selected.evaluation_sha256,
        expected_active_generation: selected.active_generation,
      });
      setReview(null);
      await refresh();
    } catch (caught: unknown) {
      setErrorMessage(caught instanceof Error ? caught.message : "L’activation a échoué.");
    } finally {
      setSubmitting(false);
    }
  };

  const preparePilot = async () => {
    if (!selected?.evaluation_id) return;
    setErrorMessage(null);
    setSubmitting(true);
    try {
      const pilotId = selected.pilot_id ?? crypto.randomUUID();
      const planned = selected.pilot_id
        ? await api.expertMemory.pilot(pilotId)
        : await api.expertMemory.createPilotPlan(selected.evaluation_id, pilotId);
      setPilot(await api.expertMemory.auditPilot(planned.id));
    } catch (caught: unknown) {
      setErrorMessage(
        caught instanceof Error ? caught.message : "La préparation du pilote a échoué.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const attestPilot = async (decision: "accept" | "reject") => {
    if (!pilot?.audit) return;
    if (!reviewer.trim() || !pilotReference.trim() || !reason.trim()) {
      setErrorMessage("Indiquez le relecteur, la référence externe et la justification du pilote.");
      return;
    }
    setErrorMessage(null);
    setSubmitting(true);
    try {
      setPilot(
        await api.expertMemory.attestPilot(pilot.id, {
          attestation_id: crypto.randomUUID(),
          reviewer_label: reviewer.trim(),
          external_reference: pilotReference.trim(),
          decision,
          reason: reason.trim(),
        }),
      );
    } catch (caught: unknown) {
      setErrorMessage(
        caught instanceof Error ? caught.message : "L'attestation du pilote a échoué.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && !data) return <LoadingState label="Lecture des candidats à revoir…" />;
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
        description="Examinez les candidats isolés. Une activation exige une décision humaine explicite et un rapport d’évaluation réussi."
        eyebrow="Mémoire experte"
        title="Revue des candidats"
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

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card>
          <CardHeader>
            <p className="font-bold text-slate-900">Candidats</p>
            <p className="mt-1 text-sm text-slate-500">
              Les textes scientifiques complets restent hors de cette liste.
            </p>
          </CardHeader>
          <CardBody>
            {data.candidates.length === 0 ? (
              <p className="text-sm text-slate-500">Aucun candidat persistant.</p>
            ) : (
              <ul className="space-y-2" aria-label="Candidats de mémoire experte">
                {data.candidates.map((candidate) => (
                  <li key={candidate.id}>
                    <button
                      className={`w-full rounded-xl border px-3 py-3 text-left transition ${
                        candidate.id === selectedId
                          ? "border-forest-500 bg-forest-50"
                          : "border-slate-200 hover:border-forest-300 hover:bg-slate-50"
                      }`}
                      onClick={() => {
                        setSelectedId(candidate.id);
                        setReview(null);
                        setPilot(null);
                        setErrorMessage(null);
                      }}
                      type="button"
                    >
                      <span className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-semibold text-slate-800">
                          {candidate.id}
                        </span>
                        <Badge tone={candidate.state === "awaiting_review" ? "warning" : "neutral"}>
                          {stateLabels[candidate.state]}
                        </Badge>
                      </span>
                      <span className="mt-1 block text-xs text-slate-500">
                        Évaluation : {candidate.evaluation_state ?? "non planifiée"}
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
              <ShieldCheck aria-hidden="true" className="size-5 text-forest-600" />
              <div>
                <p className="font-bold text-slate-900">Décision humaine</p>
                <p className="mt-1 text-sm text-slate-500">
                  Aucune action ne modifie la release sans confirmation.
                </p>
              </div>
            </div>
          </CardHeader>
          <CardBody>
            {!selected ? (
              <p className="text-sm text-slate-500">
                Sélectionnez un candidat pour commencer la revue.
              </p>
            ) : (
              <div className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-2">
                  <HashValue label="Hash candidat" value={selected.candidate_sha256} />
                  <HashValue
                    label="Hash évaluation"
                    value={selected.evaluation_sha256 ?? "Indisponible"}
                  />
                </div>
                {canReview ? (
                  <>
                    <div>
                      <label
                        className="text-sm font-semibold text-slate-800"
                        htmlFor="reviewer-label"
                      >
                        Identifiant du relecteur
                      </label>
                      <input
                        className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 px-3 text-sm outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
                        id="reviewer-label"
                        onChange={(event) => setReviewer(event.target.value)}
                        value={reviewer}
                      />
                    </div>
                    <div>
                      <label
                        className="text-sm font-semibold text-slate-800"
                        htmlFor="review-reason"
                      >
                        Justification
                      </label>
                      <textarea
                        className="mt-2 min-h-28 w-full rounded-[10px] border border-slate-300 px-3 py-2 text-sm outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
                        id="review-reason"
                        onChange={(event) => setReason(event.target.value)}
                        value={reason}
                      />
                    </div>
                    <fieldset>
                      <legend className="text-sm font-semibold text-slate-800">Décision</legend>
                      <div className="mt-2 flex flex-wrap gap-4 text-sm text-slate-700">
                        {(["approve", "reject", "needs_changes"] as const).map((value) => (
                          <label className="flex items-center gap-2" key={value}>
                            <input
                              checked={decision === value}
                              name="review-decision"
                              onChange={() => setDecision(value)}
                              type="radio"
                            />
                            {value === "approve"
                              ? "Approuver"
                              : value === "reject"
                                ? "Rejeter"
                                : "Demander des changements"}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                    <Button loading={submitting} onClick={submitReview}>
                      Enregistrer la décision
                    </Button>
                  </>
                ) : review?.decision === "approve" && selected.state === "approved" ? (
                  <div className="space-y-4">
                    <p className="flex items-center gap-2 text-sm text-emerald-800" role="status">
                      <CheckCircle2 aria-hidden="true" className="size-4" /> Revue approuvée.
                    </p>
                    <Button loading={submitting} onClick={activate}>
                      Activer explicitement la candidate
                    </Button>
                  </div>
                ) : (
                  <p className="flex items-start gap-2 text-sm leading-6 text-slate-600">
                    <TriangleAlert
                      aria-hidden="true"
                      className="mt-1 size-4 shrink-0 text-amber-600"
                    />
                    La candidate n’est pas dans un état permettant une décision, ou son évaluation
                    n’est pas réussie.
                  </p>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      {selected && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-bold text-slate-900">Pilote shadow de validation</p>
                <p className="mt-1 text-sm text-slate-500">
                  Le pilote reste validation-only et ne peut jamais activer une release.
                </p>
              </div>
              {pilot && (
                <Badge tone={pilot.state === "ready" ? "success" : "warning"}>{pilot.state}</Badge>
              )}
            </div>
          </CardHeader>
          <CardBody>
            {!selected.evaluation_id ? (
              <p className="text-sm text-slate-500">Une évaluation est requise avant le pilote.</p>
            ) : !pilot ? (
              <Button loading={submitting} onClick={preparePilot} variant="secondary">
                Préparer et auditer le pilote
              </Button>
            ) : (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                  <HashValue label="Hash du plan" value={pilot.plan_sha256} />
                  <HashValue label="Hash de l’audit" value={pilot.audit_sha256 ?? "Absent"} />
                  <HashValue label="Référence pilote" value={pilot.id} />
                </div>
                {pilot.audit?.state === "ready" && pilot.attestation?.decision !== "reject" && (
                  <PilotObservationForm
                    loading={submitting}
                    onError={setErrorMessage}
                    onSaved={setPilot}
                    pilot={pilot}
                  />
                )}
                {pilot.audit?.blockers.length ? (
                  <p className="text-sm text-amber-800" role="status">
                    Blocages : {pilot.audit.blockers.join(", ")}.
                  </p>
                ) : pilot.audit?.state === "ready" && !pilot.attestation ? (
                  <div className="grid gap-4 md:grid-cols-2">
                    <label
                      className="text-sm font-semibold text-slate-800"
                      htmlFor="pilot-reference"
                    >
                      Référence externe
                      <input
                        className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 px-3 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
                        id="pilot-reference"
                        onChange={(event) => setPilotReference(event.target.value)}
                        value={pilotReference}
                      />
                    </label>
                    <div className="flex flex-wrap items-end gap-3">
                      <Button loading={submitting} onClick={() => attestPilot("accept")}>
                        Attester pour la suite
                      </Button>
                      <Button
                        loading={submitting}
                        onClick={() => attestPilot("reject")}
                        variant="secondary"
                      >
                        Refuser le pilote
                      </Button>
                    </div>
                  </div>
                ) : pilot.attestation ? (
                  <p className="text-sm text-slate-700" role="status">
                    Décision externe : {pilot.attestation.decision} (
                    {pilot.attestation.external_reference}). L’activation reste séparée et interdite
                    par ce pilote.
                  </p>
                ) : (
                  <p className="text-sm text-slate-500">
                    Le pilote attend des corrections avant toute attestation.
                  </p>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function HashValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 break-all font-mono text-[11px] text-slate-700">{value}</p>
    </div>
  );
}
