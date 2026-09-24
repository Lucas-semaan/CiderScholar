import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import type { ExpertPilotResponse } from "@/types/api";

interface PilotObservationFormProps {
  pilot: ExpertPilotResponse;
  loading: boolean;
  onSaved: (pilot: ExpertPilotResponse) => void;
  onError: (message: string) => void;
}

export function PilotObservationForm({
  pilot,
  loading,
  onSaved,
  onError,
}: PilotObservationFormProps) {
  const [caseSha256, setCaseSha256] = useState("");
  const [expertTimeSeconds, setExpertTimeSeconds] = useState(300);
  const [diagnosisHumanCorrected, setDiagnosisHumanCorrected] = useState(false);
  const [usefulEffect, setUsefulEffect] = useState<"useful" | "neutral" | "harmful" | "unknown">(
    "unknown",
  );
  const [falseGain, setFalseGain] = useState(false);
  const [rollbackCount, setRollbackCount] = useState(0);
  const [promptTokens, setPromptTokens] = useState(0);
  const [completionTokens, setCompletionTokens] = useState(0);
  const [recordedBy, setRecordedBy] = useState("");

  const submit = async () => {
    if (!/^[0-9a-f]{64}$/.test(caseSha256) || !recordedBy.trim()) {
      onError("Le cas doit être identifié par un SHA-256 et le relecteur est requis.");
      return;
    }
    try {
      const response = await api.expertMemory.recordPilotObservation(pilot.id, {
        observation_id: crypto.randomUUID(),
        case_sha256: caseSha256,
        expert_time_seconds: expertTimeSeconds,
        diagnosis_human_corrected: diagnosisHumanCorrected,
        useful_effect: usefulEffect,
        false_gain: falseGain,
        rollback_count: rollbackCount,
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        recorded_by: recordedBy.trim(),
      });
      onSaved(response);
      setCaseSha256("");
    } catch (caught: unknown) {
      onError(caught instanceof Error ? caught.message : "L'observation n'a pas été enregistrée.");
    }
  };

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div>
        <p className="text-sm font-semibold text-slate-800">Ajouter une observation réelle</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          Saisissez uniquement les mesures et le hash du cas ; aucun texte de conversation n’est
          conservé ici.
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="SHA-256 du cas" value={caseSha256} onChange={setCaseSha256} />
        <NumberField
          label="Temps expert (secondes)"
          min={1}
          max={86400}
          value={expertTimeSeconds}
          onChange={setExpertTimeSeconds}
        />
        <NumberField
          label="Tokens prompt"
          min={0}
          max={100000}
          value={promptTokens}
          onChange={setPromptTokens}
        />
        <NumberField
          label="Tokens réponse"
          min={0}
          max={100000}
          value={completionTokens}
          onChange={setCompletionTokens}
        />
        <NumberField
          label="Retours arrière"
          min={0}
          max={10}
          value={rollbackCount}
          onChange={setRollbackCount}
        />
        <Field label="Relecteur" value={recordedBy} onChange={setRecordedBy} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm font-semibold text-slate-800" htmlFor="pilot-useful-effect">
          Effet observé
          <select
            className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 bg-white px-3 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
            id="pilot-useful-effect"
            onChange={(event) =>
              setUsefulEffect(event.target.value as "useful" | "neutral" | "harmful" | "unknown")
            }
            value={usefulEffect}
          >
            <option value="useful">Utile</option>
            <option value="neutral">Neutre</option>
            <option value="harmful">Nuisible</option>
            <option value="unknown">Inconnu</option>
          </select>
        </label>
        <p className="self-end text-xs leading-5 text-slate-500">
          Les notes libres sont volontairement exclues pour préserver la confidentialité.
        </p>
      </div>
      <div className="flex flex-wrap gap-5 text-sm text-slate-700">
        <Checkbox
          checked={diagnosisHumanCorrected}
          label="Diagnostic corrigé par l’humain"
          onChange={setDiagnosisHumanCorrected}
        />
        <Checkbox checked={falseGain} label="Faux gain observé" onChange={setFalseGain} />
      </div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          {pilot.metrics.observation_count}/20 observations, minimum 10 pour clôturer.
        </p>
        <Button loading={loading} onClick={submit} variant="secondary">
          Enregistrer l’observation
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `pilot-${label.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <label className="text-sm font-semibold text-slate-800" htmlFor={id}>
      {label}
      <input
        className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 bg-white px-3 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
        id={id}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}

function NumberField({
  label,
  min,
  max,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="text-sm font-semibold text-slate-800">
      {label}
      <input
        className="mt-2 min-h-11 w-full rounded-[10px] border border-slate-300 bg-white px-3 font-normal outline-none focus:border-forest-600 focus:ring-2 focus:ring-forest-200"
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        type="number"
        value={value}
      />
    </label>
  );
}

function Checkbox({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <input
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      {label}
    </label>
  );
}
