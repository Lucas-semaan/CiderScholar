import { describe, expect, it } from "vitest";

import { shouldOpenLibraryDetailDialog } from "@/features/library/libraryDetail";

describe("library record detail display", () => {
  it("does not open the modal when the desktop split view is visible", () => {
    expect(shouldOpenLibraryDetailDialog(true, true)).toBe(false);
  });

  it("opens the modal for an explicit selection on a narrow viewport", () => {
    expect(shouldOpenLibraryDetailDialog(true, false)).toBe(true);
  });

  it("keeps the modal closed without an explicit selection", () => {
    expect(shouldOpenLibraryDetailDialog(false, false)).toBe(false);
  });
});
