import { describe, expect, it } from "vitest";

import { BACKDROP_SCRIM, BACKDROP_SCRIM_FLOOR, scrimMask } from "../src/scenes/common";

/** The alpha of every stop in a mask, in the order they appear. */
function alphas(mask: string): number[] {
  return [...mask.matchAll(/rgba\(0,0,0,([\d.]+)\)/g)].map((m) => Number(m[1]));
}
/** The percentage each stop sits at. */
function stops(mask: string): number[] {
  return [...mask.matchAll(/\)\s([\d.]+)%/g)].map((m) => Number(m[1]));
}

describe("the backdrop veil is aimed at the type, not laid over the picture", () => {
  it("leaves a picture that is the whole scene alone", () => {
    // ImageScene asks for scrim 0: there is no type to protect and nothing to dim.
    expect(scrimMask("left", 0, 50)).toBeUndefined();
  });

  it("stays flat when a scene carries a notice", () => {
    // A caveat is pinned outside the content block, so it can land anywhere the picture is bright.
    expect(scrimMask("full", BACKDROP_SCRIM, 50)).toBeUndefined();
  });

  it("stays flat across the safe area when the scene did not measure its type", () => {
    // The conservative default: forgetting to opt in must not cost a card its contrast floor.
    const mask = scrimMask("left", BACKDROP_SCRIM);
    expect(mask).toBeDefined();
    expect(stops(mask!)).toEqual([0, 96, 100]);
  });

  it("holds full strength to where the type ends and thins past it", () => {
    const mask = scrimMask("left", BACKDROP_SCRIM, 53)!;
    const a = alphas(mask);
    const s = stops(mask);
    // Full alpha from the frame edge to the end of the type...
    expect(s[0]).toBe(0);
    expect(a[0]).toBe(1);
    expect(s[1]).toBe(53);
    expect(a[1]).toBe(1);
    // ...then monotonically down, and never back up.
    expect(a).toEqual([...a].sort((x, y) => y - x));
    // The floor is expressed as a fraction of the scrim, so the veil really lands on it. Three
    // decimals because that is what the mask string carries; more is not there to check.
    expect(a.at(-1)! * BACKDROP_SCRIM).toBeCloseTo(BACKDROP_SCRIM_FLOOR, 3);
  });

  it("does not run its roll-off off the end of the frame", () => {
    // A headline that fills the safe area: every stop still has to be a legal percentage.
    const s = stops(scrimMask("left", BACKDROP_SCRIM, 92)!);
    expect(Math.max(...s)).toBe(100);
    expect(s).toEqual([...s].sort((x, y) => x - y));
  });

  it("gives a portrait card a band down the middle and colour at both edges", () => {
    const mask = scrimMask("middle", BACKDROP_SCRIM)!;
    expect(mask).toContain("to bottom");
    const a = alphas(mask);
    expect(a.length).toBeGreaterThan(2);
    // Symmetric: top and bottom keep the same colour, and the type in the middle gets the floor.
    expect(a.at(0)!).toBeCloseTo(a.at(-1)!, 5);
    expect(Math.max(...a)).toBe(1);
  });

  it("mirrors the axis for a card whose type sits at the bottom", () => {
    expect(scrimMask("top", BACKDROP_SCRIM)).toContain("to bottom");
    expect(scrimMask("bottom", BACKDROP_SCRIM)).toContain("to top");
  });
});
