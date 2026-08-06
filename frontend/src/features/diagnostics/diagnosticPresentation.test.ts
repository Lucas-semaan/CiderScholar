import { describe, expect, it } from "vitest";

import {
  diagnosticLabels,
  diagnosticJobStateLabels,
  diagnosticJobStepLabels,
  diagnosticJobTypeLabels,
  formatBytes,
  formatDiagnosticDate,
  formatQueueAge,
  workerStatePresentation,
} from "./diagnosticPresentation";

describe("diagnostic presentation", () => {
  it("formats an empty, recent and old queue without exposing content", () => {
    expect(formatQueueAge(null)).toBe("Aucun travail en attente");
    expect(formatQueueAge(42)).toBe("42 s");
    expect(formatQueueAge(125)).toBe("2 min");
    expect(formatQueueAge(7325)).toBe("2 h 02");
  });

  it("names every required readiness check", () => {
    expect(Object.keys(diagnosticLabels).sort()).toEqual(["argo", "corpus", "disk", "worker"]);
  });

  it("formats process memory without leaking raw implementation values", () => {
    expect(formatBytes(null)).toBe("Indisponible");
    expect(formatBytes(undefined)).toBe("Indisponible");
    expect(formatBytes(512)).toBe("512 o");
    expect(formatBytes(1_572_864)).toBe("1,5 Mo");
  });

  it("translates public job metadata instead of exposing implementation values", () => {
    expect(diagnosticJobTypeLabels.chat_answer).toBe("Réponse scientifique");
    expect(diagnosticJobStateLabels.cancel_requested).toBe("Annulation demandée");
    expect(diagnosticJobStepLabels.planning).toBe("Analyse et planification de la question");
    expect(diagnosticJobStepLabels.generation).toBe("Génération de la réponse finale");
  });

  it("presents each worker state with a distinct actionable status", () => {
    expect(workerStatePresentation.healthy.tone).toBe("success");
    expect(workerStatePresentation.stale.tone).toBe("warning");
    expect(formatDiagnosticDate(null)).toBe("Aucun signal reçu");
  });
});
