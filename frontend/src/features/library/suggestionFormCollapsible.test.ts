import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SuggestionForm } from "./SuggestionForm";

describe("SuggestionForm", () => {
  it("is collapsed by default and exposes its header as an accessible disclosure control", () => {
    const markup = renderToStaticMarkup(createElement(SuggestionForm));

    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('aria-controls="suggestion-form-panel"');
    expect(markup).toContain("Proposer un document scientifique");
    expect(markup).not.toContain("Type de proposition");
    expect(markup).not.toContain("Sans suivi distant");
    expect(markup).not.toContain("maintenance hebdomadaire");
  });
});
