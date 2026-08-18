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
    expect(librarySectionFromQuery("unknown")).toBe("records");
    expect(appDestinations.corpusActivity).toBe(
      "/bibliotheque?section=pdf&tab=activity&filter=attention",
    );
  });

  it("routes corpus cards to the common corpus", () => {
    expect(corpusDestinations()).toEqual({
      articles: "/bibliotheque?section=pdf&tab=articles#articles",
      assistant: "/",
      activity: "/bibliotheque?section=pdf&tab=activity&filter=attention",
    });
  });

  it("accepts only known corpus tabs", () => {
    expect(corpusTabFromQuery("activity")).toBe("activity");
    expect(corpusTabFromQuery("import")).toBe("import");
    expect(corpusTabFromQuery("unknown")).toBe("articles");
  });
});
