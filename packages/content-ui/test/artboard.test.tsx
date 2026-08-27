import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ArtboardSpec, RenderBundle, TextLayer } from "@content-factory/content-schema-ts";
import { Artboard, DataCard, QuoteCard, legibilityReport } from "../src/index";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..", "..", "..");
const bundle = JSON.parse(readFileSync(path.join(root, "fixtures/demo/artboard-bundle.json"), "utf8")) as RenderBundle;
const artboard = bundle.artboard as ArtboardSpec;

describe("Artboard", () => {
  it("renders the fixture bundle to static markup with the resolved number and publisher", () => {
    const html = renderToStaticMarkup(<Artboard bundle={bundle} />);
    expect(html).toContain('data-artboard-id="art_demo00000001"');
    expect(html).toContain('data-value="21"');
    expect(html).toMatch(/<span[^>]*>21<\/span>/);
    expect(html).toContain("Energimyndigheten");
    expect(html).toContain("SWEDEN · ELECTRICITY · 2025");
    expect(html).toContain("of electricity came from wind");
    expect(html).toContain("width:1080px;height:1080px");
    expect(html).not.toMatch(/transition/);
  });

  it("is deterministic (identical markup on repeated renders)", () => {
    expect(renderToStaticMarkup(<Artboard bundle={bundle} />)).toBe(renderToStaticMarkup(<Artboard bundle={bundle} />));
  });

  it("renders every layer kind, including placeholders for charts and missing assets", () => {
    const spec: ArtboardSpec = {
      ...artboard,
      background_role: "ink",
      layers: [
        ...artboard.layers,
        { color_role: "accent", frame: { x: 0.08, y: 0.5, w: 0.2, h: 0.01 }, kind: "shape", layer_id: "lay_rule00000001", locked: false, radius_token: "none", reading_order: 4, shape: "rule" },
        { frame: { x: 0.5, y: 0.5, w: 0.4, h: 0.3 }, kind: "chart", layer_id: "lay_chart0000001", locked: false, reading_order: 5, scene_ref: "scn_chart000001" },
        { alt_text: "logo", asset_id: "ast_missing00001", crop: null, fit: "contain", frame: { x: 0.7, y: 0.1, w: 0.2, h: 0.1 }, kind: "image", layer_id: "lay_image0000001", locked: false, reading_order: 6 },
        { alt_text: "photo", asset_id: "ast_present00001", crop: { x: 0.1, y: 0.1, w: 0.5, h: 0.5 }, fit: "cover", frame: { x: 0.7, y: 0.3, w: 0.2, h: 0.1 }, kind: "image", layer_id: "lay_image0000002", locked: false, reading_order: 7 },
      ],
    };
    const html = renderToStaticMarkup(<Artboard bundle={{ ...bundle, artboard: spec, assets: { ast_present00001: "assets/photo.png" } }} assetUrl={(_id, p) => `/static/${p}`} />);
    expect(html).toContain("chart · scn_chart000001");
    expect(html).toContain("missing asset · ast_missing00001");
    expect(html).toContain('src="/static/assets/photo.png"');
    expect(html).toContain('data-layer-kind="shape"');
  });

  it("DataCard and QuoteCard wrappers render through Artboard", () => {
    const sources = Object.values(bundle.sources);
    const card = renderToStaticMarkup(<DataCard label="Sweden · Wind" value={21} unit="%" format="integer" headline="of electricity came from wind" sources={sources} />);
    expect(card).toContain('data-value="21"');
    expect(card).toContain("Energimyndigheten");
    const quote = renderToStaticMarkup(<QuoteCard quote="The grid is the story." attribution="A. Analyst" sources={sources.slice(0, 1)} />);
    expect(quote).toContain("The grid is the story.");
    expect(quote).toContain("A. Analyst");
  });
});

describe("legibilityReport", () => {
  const ctx = { datasets: bundle.datasets, sources: bundle.sources };

  it("passes the fixture artboard at 1080 and reports sizes normalized to 1080", () => {
    const r = legibilityReport(artboard, 1080, { context: ctx, brand: bundle.brand });
    expect(r.findings.filter((f) => f.severity !== "advisory")).toEqual([]);
    expect(r.ok).toBe(true);
    expect(r.minFontPx1080).not.toBeNull();
    expect(r.minFontPx1080 as number).toBeGreaterThanOrEqual(24);
    expect(r.layers.find((l) => l.layer_id === "lay_number000001")?.fontPx).toBe(220);
    expect(r.contrast.every((c) => c.pass)).toBe(true);
    // Rendering at 2160 wide scales sizes but keeps the 1080-normalized values.
    const big = legibilityReport(artboard, 2160, { context: ctx });
    expect(big.height).toBe(2160);
    expect(big.minFontPx1080).toBe(r.minFontPx1080);
  });

  it("flags a deliberately tiny layer and missing data/sources", () => {
    const tiny: TextLayer = { align: "start", frame: { x: 0.1, y: 0.95, w: 0.3, h: 0.015 }, kind: "text", layer_id: "lay_tiny00000001", locked: false, max_lines: 1, reading_order: 9, role: "body", text: { claim_ids: [], text: "A footnote that has to squeeze into a sliver of space" } };
    const spec: ArtboardSpec = { ...artboard, layers: [...artboard.layers, tiny] };
    const r = legibilityReport(spec, 1080, { context: ctx });
    expect(r.ok).toBe(false);
    const small = r.findings.find((f) => f.code === "font_too_small" && f.layer_id === "lay_tiny00000001");
    expect(small).toBeDefined();
    expect(small?.value as number).toBeLessThan(24);
    expect(r.findings.some((f) => f.code === "outside_safe_area" && f.layer_id === "lay_tiny00000001")).toBe(true);
    expect(r.minFontPx1080 as number).toBeLessThan(24);

    const noData = legibilityReport(artboard, 1080); // no context: number and source unresolved
    expect(noData.findings.map((f) => f.code)).toEqual(expect.arrayContaining(["missing_data", "missing_source"]));
  });

  it("flags low contrast when brand overrides break the palette", () => {
    const r = legibilityReport(artboard, 1080, { context: ctx, brand: { accent: "#DDDDDD", paper: null, ink: null, font_family: "Inter", logo_asset_id: null } });
    expect(r.findings.some((f) => f.code === "contrast_low")).toBe(true);
    expect(r.ok).toBe(false);
  });
});
