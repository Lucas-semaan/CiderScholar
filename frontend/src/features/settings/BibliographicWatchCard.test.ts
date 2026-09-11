import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WatchReport } from "@/types/api";

import { WatchForm, WatchSummary } from "./BibliographicWatchCard";

describe("bibliographic watch", () => {
  it("labels configuration controls and explains activation and catchup", () => {
    const html = renderToStaticMarkup(
      createElement(WatchForm, {
        configuration: {
          enabled: false,
          themes: [{ key: "microbiologie", query: "cider fermentation" }],
        },
        save: async () => {},
        busy: false,
      }),
    );
    expect(html).toContain("Activer la veille hebdomadaire");
    expect(html).toContain("rattrapée à la réouverture");
    expect(html).toContain("Mots-clés");
    expect(html).toContain('aria-label="Retirer le thème microbiologie"');
    expect(html).toContain('type="submit"');
  });

  it("distinguishes indexed additions from discoveries, failures and deferred contents", () => {
    const report: WatchReport = {
      job_id: "watch-1",
      state: "partial",
      started_at: "2026-09-10T12:00:00Z",
      completed_at: null,
      examined: 500,
      duplicates: 200,
      accepted: 30,
      review: 12,
      rejected: 250,
      acquisitions_attempted: 30,
      added_to_rag: 5,
      full_articles: 2,
      abstracts_only: 3,
      deferred: 25,
      errors: ["crossref: deferred_429"],
      limitations: [],
    };
    const html = renderToStaticMarkup(createElement(WatchSummary, { report }));
    expect(html).toContain("5 ajout(s) au RAG");
    expect(html).toContain("500 notices examinées");
    expect(html).toContain("25 différées");
    expect(html).toContain("Partiel");
    expect(html).toContain("crossref: deferred_429");
    expect(html).not.toContain("500 ajout(s)");
  });
});
