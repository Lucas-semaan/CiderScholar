import { describe, expect, it } from "vitest";

import {
  appDestinations,
  corpusDestinations,
  corpusTabFromQuery,
  librarySectionFromQuery,
} from "@/lib/navigation";

describe("navigation destinations", () => {
  it("routes every dashboard metric to a useful screen", () => {
    expect(appDestinations.documentaryRecords).toBe("/bibliotheque");
    expect(appDestinations.localPdfs).toBe("/bibliotheque?section=pdf&tab=articles");
    expect(appDestinations.scientificAssistant).toBe("/");
    expect(appDestinations.savedAnalyses).toBe("/syntheses");
  });

  it("keeps PDF documents inside the documentary database", () => {
    expect(librarySectionFromQuery("pdf")).toBe("pdf");
    expect(librarySectionFromQuery("private")).toBe("records");
    expect(librarySectionFromQuery("unknown")).toBe("records");
    expect(appDestinations.corpusActivity).toBe("/bibliotheque?section=pdf&tab=activity");
  });

  it("routes common and private corpus cards to their own content", () => {
    expect(corpusDestinations("common")).toEqual({
      articles: "/bibliotheque?section=pdf&tab=articles#articles",
      assistant: "/",
      activity: "/bibliotheque?section=pdf&tab=activity",
    });
    expect(corpusDestinations("private")).toEqual({
      articles: "/bibliotheque?section=private&tab=articles#articles",
      assistant: "/",
      activity: "/bibliotheque?section=private&tab=activity",
    });
  });

  it("accepts only known corpus tabs", () => {
    expect(corpusTabFromQuery("activity")).toBe("activity");
    expect(corpusTabFromQuery("import")).toBe("import");
    expect(corpusTabFromQuery("unknown")).toBe("articles");
  });
});
