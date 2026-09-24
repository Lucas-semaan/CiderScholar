import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ExpertCorrectionDialog } from "./ExpertCorrectionDialog";

describe("ExpertCorrectionDialog", () => {
  it("renders an accessible private-correction form with both scopes", () => {
    const markup = renderToStaticMarkup(
      createElement(ExpertCorrectionDialog, {
        messageId: "message-1",
        messageContent: "Réponse scientifique locale.",
        onClose: () => undefined,
      }),
    );

    expect(markup).toContain("Proposer une correction");
    expect(markup).toContain("Problème observé");
    expect(markup).toContain("Correction proposée");
    expect(markup).toContain("Cette réponse uniquement");
    expect(markup).toContain("Méthode réutilisable à revoir");
    expect(markup).toContain("Enregistrer la proposition");
  });
});
