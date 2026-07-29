import { useCallback, useState } from "react";

import { CalendarClock, Play, TimerReset } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ErrorState, LoadingState } from "@/components/ui/Feedback";
import { useRemoteData } from "@/hooks/useRemoteData";
import { api } from "@/lib/api";

export function AdminMaintenanceCard() {
  const load = useCallback(() => api.adminMaintenance.status(), []);
  const schedule = useRemoteData(load);
  const [busy, setBusy] = useState<"launch" | "defer" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (schedule.loading && !schedule.data) return <LoadingState label="Échéance administrateur…" />;
  if (schedule.error && !schedule.data)
    return <ErrorState message={schedule.error} retry={schedule.refresh} />;
  if (!schedule.data) return null;

  const launch = async () => {
    setBusy("launch");
    try {
      const job = await api.adminMaintenance.launch();
      setMessage(`Maintenance ${job.id} ajoutée à la file locale.`);
      schedule.refresh();
    } finally {
      setBusy(null);
    }
  };
  const defer = async () => {
    setBusy("defer");
    try {
      await api.adminMaintenance.defer();
      setMessage("Rappel reporté pour ce lancement. L’échéance réelle reste inchangée.");
      schedule.refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CalendarClock className="size-5 text-cider-600" />
            <h2 className="font-bold text-slate-900">Maintenance administrateur</h2>
          </div>
          <Badge tone={schedule.data.due ? "warning" : "success"}>
            {schedule.data.due ? "Échue" : "À jour"}
          </Badge>
        </div>
      </CardHeader>
      <CardBody className="space-y-4 text-sm text-slate-600">
        <p>
          Dernier succès :{" "}
          {schedule.data.last_success
            ? `${new Date(schedule.data.last_success.completed_at).toLocaleString("fr-FR")} · ${schedule.data.last_success.corpus_version}`
            : "aucune maintenance publiée"}
        </p>
        {message && <p className="rounded-xl bg-sky-50 px-3 py-2 text-sky-800">{message}</p>}
        {schedule.data.prompt && (
          <div className="flex flex-wrap gap-3">
            <Button loading={busy === "launch"} onClick={() => void launch()}>
              <Play className="size-4" /> Lancer maintenant
            </Button>
            <Button loading={busy === "defer"} onClick={() => void defer()} variant="secondary">
              <TimerReset className="size-4" /> Reporter
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
