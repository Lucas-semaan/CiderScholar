import { CheckCircle2 } from "lucide-react";

import { Card, CardBody } from "@/components/ui/Card";

interface SettingsFeedbackProps {
  error: string | null;
  health: Record<string, unknown> | null;
  message: string | null;
  modelName: string;
}

export function SettingsFeedback({ error, health, message, modelName }: SettingsFeedbackProps) {
  return (
    <div aria-live="polite" className="space-y-3">
      {(message || error) && (
        <div
          className={
            error
              ? "rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
              : "rounded-xl border border-forest-200 bg-forest-50 px-4 py-3 text-sm text-forest-700"
          }
          role={error ? "alert" : "status"}
        >
          {error ?? message}
        </div>
      )}
      {health && (
        <Card className="border-forest-200 bg-forest-50">
          <CardBody className="flex items-center gap-3 py-3 text-sm text-forest-800">
            <CheckCircle2 aria-hidden="true" className="size-5" />
            Moteur joignable : {String(health.configured_model ?? health.model ?? modelName)}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
