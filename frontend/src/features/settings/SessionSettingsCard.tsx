import { Save } from "lucide-react";
import type { FormEventHandler } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Form";
import type { RuntimeSettings } from "@/types/api";

interface SessionSettingsCardProps {
  busy: boolean;
  onSave: FormEventHandler<HTMLFormElement>;
  settings: RuntimeSettings;
}

export function SessionSettingsCard({ busy, onSave, settings }: SessionSettingsCardProps) {
  const retrieval = settings.retrieval;

  return (
    <Card className="w-full xl:max-w-2xl">
      <CardHeader>
        <h2 className="font-bold text-slate-900">Configuration de session</h2>
        <p className="mt-1 text-xs text-slate-500">
          Validée par les mêmes contrats Pydantic que config.yaml
        </p>
        <p className="mt-3 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-800">
          Mémoire détectée : {settings.memory.detected_total_gb ?? "indisponible"} Go · profil
          recommandé : {settings.memory.recommended_profile ?? "aucun"} · profil actif :{" "}
          {settings.memory.active_profile}. La recommandation n’est jamais appliquée
          automatiquement.
        </p>
      </CardHeader>
      <CardBody>
        <form
          className="grid gap-x-4 gap-y-5 sm:grid-cols-2"
          key={retrieval.default_article_count}
          onSubmit={onSave}
        >
          <Field label="Articles par recherche">
            <Input
              defaultValue={retrieval.default_article_count}
              max={100}
              min={1}
              name="default_article_count"
              type="number"
            />
          </Field>
          <Field hint="Poids de la recherche plein texte" label="Poids lexical">
            <Input
              defaultValue={retrieval.lexical_weight}
              max={1}
              min={0}
              name="lexical_weight"
              step="0.05"
              type="number"
            />
          </Field>
          <Field hint="Poids de la proximité sémantique" label="Poids vectoriel">
            <Input
              defaultValue={retrieval.vector_weight}
              max={1}
              min={0}
              name="vector_weight"
              step="0.05"
              type="number"
            />
          </Field>
          <Field hint="Les trois poids doivent totaliser 1" label="Poids reranker">
            <Input
              defaultValue={retrieval.reranker_weight}
              max={1}
              min={0}
              name="reranker_weight"
              step="0.05"
              type="number"
            />
          </Field>
          <Field label="Lot d’embeddings">
            <Input
              defaultValue={settings.embedding_batch_size}
              max={64}
              min={1}
              name="embedding_batch_size"
              type="number"
            />
          </Field>
          <Field label="Passages par article">
            <Input
              defaultValue={settings.passages_per_article}
              max={8}
              min={1}
              name="passages_per_article"
              type="number"
            />
          </Field>
          <div className="flex items-end pt-1 sm:col-span-2 sm:justify-end">
            <Button className="w-full sm:w-auto" loading={busy} type="submit">
              <Save aria-hidden="true" className="size-4" />
              Appliquer
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
