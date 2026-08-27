import { describe, expect, it } from "vitest";
import { fuzzyScore } from "../src";

describe("fuzzyScore", () => {
  it("matches subsequences and ranks prefix/word-start hits higher", () => {
    expect(fuzzyScore("gi", "Go to Inbox")).not.toBeNull();
    expect(fuzzyScore("xyz", "Go to Inbox")).toBeNull();
    expect(fuzzyScore("", "anything")).toBe(0);
    const prefix = fuzzyScore("go", "Go to Inbox")!;
    const scattered = fuzzyScore("go", "Toggle dark mode")!;
    expect(prefix).toBeGreaterThan(scattered);
  });
});
