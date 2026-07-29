import { describe, expect, it } from "vitest";

import { diagnosticLabels, formatQueueAge } from "./diagnosticPresentation";

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
});
