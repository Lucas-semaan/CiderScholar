import { describe, expect, it } from "vitest";

import { formatResponseTime } from "./chatSession";

describe("chat session", () => {
  it("formats response times in tenths of a second for the French interface", () => {
    expect(formatResponseTime(0)).toBe("0,0 s");
    expect(formatResponseTime(1240)).toBe("1,2 s");
    expect(formatResponseTime(1280)).toBe("1,3 s");
    expect(formatResponseTime(-100)).toBe("0,0 s");
  });
});
