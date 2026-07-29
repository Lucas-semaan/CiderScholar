import { type FormEvent, useCallback, useState } from "react";

import { AlertTriangle, MessageSquareWarning } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/Form";
import { PageHeader } from "@/components/ui/PageHeader";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/cn";
import type { PilotDefectType } from "@/types/api";

import { buildPilotDefectPayload, pilotDefectTypeLabels } from "./pilotFeedback";

export function PilotFeedbackPage() {
  const loadDefects = useCallback(() => api.pilotFeedback.list(), []);
  const defects = useRemoteData(loadDefects);
  const [type, setType] = useState<PilotDefectType>("functional");
  const [step, setStep] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setSubmissionError(null);
    setReceipt(null);
    try {
      const created = await api.pilotFeedback.submit(
        buildPilotDefectPayload(type, step, description),
      );
      setReceipt(created.id);
      setStep("");
      setDescription("");
      await defects.refresh();
    } catch (error) {
      setSubmissionError(error instanceof Error ? error.message : "Enregistrement impossible.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        description="Consignez un défaut local sans joindre automatiquement une conversation, un document ou un identifiant de travail."
        eyebrow="Pilote"
        title="Retours pilote"
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.8fr)]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <MessageSquareWarning aria-hidden="true" className="size-5 text-forest-600" />
              <div>
                <h2 className="font-bold text-slate-900">Décrire le défaut</h2>
                <p className="mt-1 text-xs text-slate-500">Trois champs volontaires uniquement</p>
              </div>
            </div>
          </CardHeader>
          <CardBody>
            <div className="mb-5 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
              <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
              <p>
                Ne copiez aucune question, réponse scientifique, donnée personnelle ou information
                privée. Décrivez seulement le comportement observé.
              </p>
            </div>
            <form className="grid gap-5" onSubmit={submit}>
              <Field label="Type">
                <Select
                  onChange={(event) => setType(event.target.value as PilotDefectType)}
                  value={type}
                >
                  {Object.entries(pilotDefectTypeLabels).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Étape" hint="Exemple : installation, redémarrage ou mise à jour.">
                <Input
                  maxLength={80}
                  onChange={(event) => setStep(event.target.value)}
                  required
                  value={step}
                />
              </Field>
              <Field
                label="Description volontaire"
                hint="1 500 caractères maximum, sans contenu de chat."
              >
                <Textarea
                  maxLength={1500}
                  onChange={(event) => setDescription(event.target.value)}
                  required
                  value={description}
                />
              </Field>
              <div aria-live="polite" className="min-h-5">
                {submissionError && (
                  <p className="text-sm text-red-700" role="alert">
                    {submissionError}
                  </p>
                )}
                {receipt && (
                  <p className="text-sm text-emerald-700" role="status">
                    Retour enregistré localement : {receipt}
                  </p>
                )}
              </div>
              <Button
                disabled={!step.trim() || !description.trim()}
                loading={submitting}
                type="submit"
              >
                Enregistrer le retour
              </Button>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="font-bold text-slate-900">Retours locaux récents</h2>
          </CardHeader>
          <CardBody className="space-y-3">
            {defects.loading && !defects.data && <LoadingState label="Chargement des retours…" />}
            {defects.error && !defects.data && (
              <ErrorState message={defects.error} retry={defects.refresh} />
            )}
            {defects.error && defects.data && (
              <ErrorState message={defects.error} retry={defects.refresh} />
            )}
            {defects.data?.length === 0 && (
              <p className="text-sm text-slate-500">Aucun défaut consigné sur ce poste.</p>
            )}
            {defects.data?.map((defect) => (
              <article className="rounded-xl border border-slate-200 p-4" key={defect.id}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Badge tone={defect.type === "blocking" ? "warning" : "neutral"}>
                    {pilotDefectTypeLabels[defect.type]}
                  </Badge>
                  <time className="text-xs text-slate-400" dateTime={defect.created_at}>
                    {formatDate(defect.created_at)}
                  </time>
                </div>
                <p className="mt-3 text-sm font-semibold text-slate-800">{defect.step}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600">
                  {defect.description}
                </p>
              </article>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
