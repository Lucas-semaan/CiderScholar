import { describe, expect, it } from "vitest";

import { buildPilotDefectPayload } from "./pilotFeedback";

describe("pilot defect payload", () => {
  it("contains only the three voluntary fields", () => {
    const payload = buildPilotDefectPayload(
      "usability",
      "  Redémarrage  ",
      "  Le bouton est difficile à trouver.  ",
    );

    expect(payload).toEqual({
      type: "usability",
      step: "Redémarrage",
      description: "Le bouton est difficile à trouver.",
    });
    expect(Object.keys(payload)).toEqual(["type", "step", "description"]);
  });
});
