import type { PilotDefectInput, PilotDefectType } from "@/types/api";

export const pilotDefectTypeLabels: Record<PilotDefectType, string> = {
  blocking: "Bloquant",
  functional: "Fonctionnel",
  usability: "Utilisabilité",
  performance: "Performance",
  other: "Autre",
};

export function buildPilotDefectPayload(
  type: PilotDefectType,
  step: string,
  description: string,
): PilotDefectInput {
  return { type, step: step.trim(), description: description.trim() };
}
