import { describe, expect, it } from "vitest";

import { validateSuggestionPdf } from "./suggestionPdfValidation";

describe("validateSuggestionPdf", () => {
  it("rejects a wrong extension immediately", async () => {
    const file = new File(["%PDF-1.7"], "paper.txt", { type: "text/plain" });
    await expect(validateSuggestionPdf(file)).resolves.toContain(".pdf");
  });

  it("rejects a fake PDF signature immediately", async () => {
    const file = new File(["not-pdf"], "paper.pdf", { type: "application/pdf" });
    await expect(validateSuggestionPdf(file)).resolves.toContain("signature PDF");
  });

  it("accepts a bounded PDF signature", async () => {
    const file = new File(["%PDF-1.7\nbody"], "paper.pdf", { type: "application/pdf" });
    await expect(validateSuggestionPdf(file)).resolves.toBeNull();
  });
});
