import { describe, expect, it } from "vitest";

import { publicationSource, publicationTypeLabel, themeLabel } from "./libraryPresentation";

describe("themeLabel", () => {
  it("presents persisted theme codes as readable French labels", () => {
    expect(themeLabel("aromes_procede")).toBe("Arômes et procédés");
    expect(themeLabel("calvados_eau_vie")).toBe("Calvados et eaux-de-vie");
    expect(themeLabel("cidre")).toBe("Cidre");
    expect(themeLabel("jus_pomme")).toBe("Jus de pomme");
    expect(themeLabel("polyphenols")).toBe("Polyphénols");
    expect(themeLabel("proteines")).toBe("Protéines");
    expect(themeLabel("manual_istex")).toBe("Import manuel ISTEX");
  });

  it("removes underscores from an unknown persisted theme", () => {
    expect(themeLabel("nouveau_theme")).toBe("Nouveau theme");
  });
});

describe("publicationSource", () => {
  it("uses the journal label for journal articles", () => {
    expect(
      publicationSource({
        work_type: "journal-article",
        journal: "Food Chemistry",
        publisher: "Elsevier",
      }),
    ).toEqual({ label: "Journal", value: "Food Chemistry" });
  });

  it("presents the persisted work type in user-facing French", () => {
    expect(publicationTypeLabel("journal-article")).toBe("Article de revue");
    expect(publicationTypeLabel("book-chapter")).toBe("Chapitre d’ouvrage");
    expect(publicationTypeLabel("dissertation")).toBe("Thèse ou mémoire");
    expect(publicationTypeLabel("conference-poster")).toBe("Poster scientifique");
    expect(publicationTypeLabel("supplementary-material")).toBe("Matériel supplémentaire");
  });

  it("uses the publisher label for books", () => {
    expect(
      publicationSource({
        work_type: "book",
        journal: null,
        publisher: "Springer Nature",
      }),
    ).toEqual({ label: "Éditeur", value: "Springer Nature" });
  });

  it("uses a neutral publication label when the work type is unavailable", () => {
    expect(
      publicationSource({ work_type: null, journal: "Unknown venue", publisher: null }),
    ).toEqual({ label: "Publication", value: "Unknown venue" });
  });

  it("uses an institutional label for reports and a deposit label for preprints", () => {
    expect(
      publicationSource({ work_type: "report-component", journal: null, publisher: "INRAE" }),
    ).toEqual({ label: "Institution éditrice", value: "INRAE" });
    expect(
      publicationSource({ work_type: "posted-content", journal: "HAL", publisher: null }),
    ).toEqual({ label: "Plateforme de dépôt", value: "HAL" });
  });
});
