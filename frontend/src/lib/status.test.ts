import { describe, expect, it } from "vitest";

import { statusTone } from "@/lib/status";

describe("statusTone", () => {
  it("maps workflow states to the shared semantic palette", () => {
    expect(statusTone("indexed")).toBe("success");
    expect(statusTone("pending")).toBe("warning");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("unknown")).toBe("neutral");
  });
});
