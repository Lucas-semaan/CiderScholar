import { describe, expect, it } from "vitest";

import { extractSuggestionPdfMetadata, mergeSuggestionPdfMetadata } from "./suggestionPdfMetadata";

describe("extractSuggestionPdfMetadata", () => {
  it("reads conservative, local metadata from a PDF", async () => {
    const file = new File(
      ["%PDF-1.7\n<< /Title (A study of cider) >>\n10.1000/cider.42"],
      "paper.pdf",
      { type: "application/pdf" },
    );

    await expect(extractSuggestionPdfMetadata(file)).resolves.toEqual({
      title: "A study of cider",
      doi: "10.1000/cider.42",
    });
  });

  it("does not infer metadata that is absent", async () => {
    const file = new File(["%PDF-1.7\n"], "paper.pdf", { type: "application/pdf" });

    await expect(extractSuggestionPdfMetadata(file)).resolves.toEqual({});
  });

  it("removes a metadata delimiter without stripping balanced DOI parentheses", async () => {
    const file = new File(["%PDF-1.7\n<< /Subject (DOI 10.1000/cider(test)) >>"], "paper.pdf", {
      type: "application/pdf",
    });

    await expect(extractSuggestionPdfMetadata(file)).resolves.toEqual({
      doi: "10.1000/cider(test)",
    });
  });

  it("preserves a field edited while metadata extraction is in progress", () => {
    expect(
      mergeSuggestionPdfMetadata(
        { title: "Titre corrigé", doi: "" },
        { title: "Titre du PDF", doi: "10.1000/cider.42" },
        new Set<"title" | "doi">(["title"]),
      ),
    ).toEqual({ title: "Titre corrigé", doi: "10.1000/cider.42" });
  });
});
