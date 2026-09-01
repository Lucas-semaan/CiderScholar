import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LlmProviderSettingsCards } from "./LlmProviderSettingsCards";

describe("LlmProviderSettingsCards", () => {
  it("renders two exclusive, labelled provider choices and protects the ARGO endpoint", () => {
    const markup = renderToStaticMarkup(
      createElement(LlmProviderSettingsCards, {
        busy: null,
        onActivate: () => undefined,
        onDelete: () => undefined,
        onSave: () => undefined,
        onTest: () => undefined,
        providers: [
          {
            active: true,
            base_url: "https://chatbot.argo.inrae.fr/api",
            endpoint_editable: false,
            id: "argo",
            key_configured: true,
            label: "ARGO INRAE",
            model: "argo-model",
          },
          {
            active: false,
            base_url: "https://llm.example.test/v1",
            endpoint_editable: true,
            id: "custom",
            key_configured: false,
            label: "Fournisseur personnalisé",
            model: "my-model",
          },
        ],
      }),
    );

    expect(markup).toContain('role="radiogroup"');
    expect(markup).toContain('name="active-llm-provider"');
    expect(markup.match(/type="radio"/g)).toHaveLength(2);
    expect(markup).toContain('aria-label="Utiliser ARGO INRAE"');
    expect(markup).toContain('readOnly=""');
    expect(markup).toContain('name="base_url"');
    expect(markup).toContain('name="model"');
  });
});
