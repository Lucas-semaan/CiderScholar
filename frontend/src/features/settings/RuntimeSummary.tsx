import { BrainCircuit, Cpu, Database, KeyRound, Network, type LucideIcon } from "lucide-react";

import { Card, CardBody } from "@/components/ui/Card";
import type { LlmProviderProfile, RuntimeSettings } from "@/types/api";

export function RuntimeSummary({
  settings,
  provider,
}: {
  settings: RuntimeSettings;
  provider: LlmProviderProfile | undefined;
}) {
  const cards: Array<{ icon: LucideIcon; label: string; value: string }> = [
    {
      icon: BrainCircuit,
      label: "Génération",
      value: `${provider?.label ?? settings.llm_provider.toUpperCase()} · ${settings.llm_model}`,
    },
    {
      icon: Network,
      label: "Endpoint actif",
      value: provider?.base_url ?? "Non disponible",
    },
    {
      icon: Cpu,
      label: "Embeddings",
      value: `${settings.embedding_model} · ${settings.embedding_device}`,
    },
    { icon: Database, label: "Base", value: settings.database_name },
    {
      icon: KeyRound,
      label: "Clé du moteur",
      value: settings.llm_key_configured ? "Configurée" : "Non configurée",
    },
  ];

  return (
    <section
      aria-label="Environnement d’exécution"
      className="grid gap-4 md:grid-cols-2 xl:grid-cols-5"
    >
      {cards.map(({ icon: Icon, label, value }) => (
        <Card key={label}>
          <CardBody className="flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-forest-50 text-forest-700">
              <Icon aria-hidden="true" className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-slate-400">{label}</p>
              <p className="mt-1 truncate text-sm font-bold text-slate-800" title={value}>
                {value}
              </p>
            </div>
          </CardBody>
        </Card>
      ))}
    </section>
  );
}
