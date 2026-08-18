import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RuntimeSettings } from "@/types/api";

import { SessionSettingsCard } from "./SessionSettingsCard";

const settings = {
  embedding_batch_size: 16,
  passages_per_article: 4,
  memory: {
    active_profile: "16gb",
    detected_total_gb: 16,
    recommended_profile: "16gb",
  },
  retrieval: {
    default_article_count: 20,
    lexical_weight: 0.3,
    reranker_weight: 0.4,
    vector_weight: 0.3,
  },
} as RuntimeSettings;

describe("SessionSettingsCard", () => {
  it("keeps a compact desktop width while preserving a two-column responsive form", () => {
    const markup = renderToStaticMarkup(
      createElement(SessionSettingsCard, {
        busy: false,
        onSave: () => undefined,
        settings,
      }),
    );

    expect(markup).toContain("xl:max-w-2xl");
    expect(markup).toContain("grid gap-x-4 gap-y-5 sm:grid-cols-2");
    expect(markup).toContain("sm:col-span-2 sm:justify-end");
    expect(markup).toContain('type="submit"');
  });
});
