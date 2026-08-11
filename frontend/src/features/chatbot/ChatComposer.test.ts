import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatComposer } from "./ChatComposer";
import { ChatModeMenu } from "./ChatModeMenu";

const props = {
  disabled: false,
  draft: "Question scientifique",
  error: null,
  externalSources: false,
  allowExternalSources: false,
  deepResearch: false,
  allowDeepResearch: false,
  figureAnalysis: false,
  figureAnalysisAvailable: true,
  figureAnalysisEstimate: "+12 à 18 min",
  interactionMode: "auto" as const,
  answerEffort: "balanced" as const,
  conversationContextAvailable: false,
  showSuggestions: false,
  onDraftChange: () => undefined,
  onExternalSourcesChange: () => undefined,
  onDeepResearchChange: () => undefined,
  onFigureAnalysisChange: () => undefined,
  onInteractionModeChange: () => undefined,
  onAnswerEffortChange: () => undefined,
  onSubmit: () => undefined,
};

describe("ChatComposer administrator enrichment", () => {
  it("offers the mode switch from the plus button", () => {
    const markup = renderToStaticMarkup(createElement(ChatComposer, props));

    expect(markup).toContain('aria-label="Composer une demande"');
    expect(markup).toContain("Changer de mode");
    expect(markup).not.toContain("rounded-full");
    expect(markup).not.toContain("CiderScholar choisit entre nouvelle recherche et échange");
    expect(markup).not.toContain("Réponse en prose par défaut");
  });

  it("offers local figure analysis with its estimated time in the plus menu", () => {
    const markup = renderToStaticMarkup(
      createElement(ChatModeMenu, {
        conversationContextAvailable: false,
        figureAnalysis: false,
        figureAnalysisAvailable: true,
        figureAnalysisEstimate: "+12 à 18 min",
        interactionMode: "research",
        onChoose: () => undefined,
        onEscape: () => undefined,
        onToggleFigures: () => undefined,
      }),
    );

    expect(markup).toContain("Inclure les figures");
    expect(markup).toContain("+12 à 18 min");
    expect(markup).toContain('role="menuitemcheckbox"');
  });

  it("offers a compact accessible balanced effort menu next to the settings button", () => {
    const markup = renderToStaticMarkup(createElement(ChatComposer, props));

    expect(markup).toContain('aria-label="Régler l’effort de réponse : Équilibré"');
    expect(markup).toContain("Équilibré");
    expect(markup).toContain('aria-haspopup="menu"');
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain("lucide-chevron-down");
    expect(markup).not.toContain("Intensité de la réponse");
    expect(markup).not.toContain("Ajuste la longueur");
  });

  it("hides the external API toggle from user profiles", () => {
    const markup = renderToStaticMarkup(
      createElement(ChatComposer, { ...props, allowExternalSources: false }),
    );
    expect(markup).not.toContain("APIs bibliographiques");
  });

  it("shows the external API toggle on the local administrator profile", () => {
    const markup = renderToStaticMarkup(
      createElement(ChatComposer, { ...props, allowExternalSources: true }),
    );
    expect(markup).toContain("APIs bibliographiques");
  });

  it("shows deep research only after the promotion gate", () => {
    const hidden = renderToStaticMarkup(
      createElement(ChatComposer, { ...props, allowExternalSources: false }),
    );
    const promoted = renderToStaticMarkup(
      createElement(ChatComposer, {
        ...props,
        allowExternalSources: false,
        allowDeepResearch: true,
      }),
    );

    expect(hidden).not.toContain("Analyse approfondie");
    expect(promoted).toContain("Analyse approfondie");
  });
});
