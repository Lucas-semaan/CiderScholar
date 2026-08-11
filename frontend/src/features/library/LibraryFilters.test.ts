import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LibraryFilters } from "./LibraryFilters";
import { initialLibraryFilters } from "./libraryPresentation";

describe("LibraryFilters", () => {
  it("keeps the remaining filters on one desktop row without a source selector", () => {
    const markup = renderToStaticMarkup(
      createElement(LibraryFilters, {
        filters: initialLibraryFilters,
        themes: ["jus_pomme", "polyphenols"],
        onChange: () => undefined,
        onSubmit: () => undefined,
      }),
    );

    expect(markup).not.toContain(">Source<");
    expect(markup).not.toContain("Toutes les sources");
    expect(markup).toContain('value="jus_pomme">Jus de pomme</option>');
    expect(markup).toContain('value="polyphenols">Polyphénols</option>');
    expect(markup).toContain(
      "lg:grid-cols-[minmax(0,2fr)_minmax(10rem,1fr)_minmax(10rem,1fr)_auto]",
    );
    expect(markup).toContain(">Appliquer</button>");
  });
});
