import { vi } from "vitest";

/**
 * Installs the jsdom gaps that React Aria, React Flow and the theme engine touch.
 *
 * Every shim is guarded, so this is safe to call from any suite and in any order: a real
 * implementation (or an earlier call) is never overwritten. One copy here replaces the block
 * that was pasted verbatim into three test/setup.ts files.
 */
export function installJsdomShims(): void {
  if (typeof window.matchMedia !== "function") {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  }
  if (typeof globalThis.ResizeObserver === "undefined") {
    class RO {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = RO as unknown as typeof ResizeObserver;
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  if (typeof window.scrollTo !== "function") window.scrollTo = () => {};
}
