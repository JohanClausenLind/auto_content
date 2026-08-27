// Legibility QC over an ArtboardSpec: font sizes normalized to the 1080 reference, line counts,
// truncation, contrast of every used text role against the artboard background.
import type { ArtboardSpec, BrandTokens } from "@content-factory/content-schema-ts";

import { contrastRatio } from "../tokens/color";
import { backgroundColor, contrastMatrix, resolveTheme, textColor, type ContentTheme, type ContrastCheck, type TextRole } from "../tokens/theme";
import { EMPTY_CONTEXT, layoutArtboard, type LayoutContext } from "../components/layout";

export type FindingCode =
  | "font_too_small"
  | "truncated"
  | "overflow"
  | "too_many_lines"
  | "contrast_low"
  | "outside_safe_area"
  | "missing_data"
  | "missing_source";

export type FindingSeverity = "blocker" | "major" | "advisory";

export interface LegibilityFinding {
  code: FindingCode;
  severity: FindingSeverity;
  layer_id: string | null;
  message: string;
  value?: number;
  threshold?: number;
}

export interface LayerLegibility {
  layer_id: string;
  kind: string;
  role: TextRole | null;
  fontPx: number | null;
  /** Font size normalized to a 1080 short side. */
  fontPx1080: number | null;
  lines: number | null;
  maxLines: number | null;
  truncated: boolean;
}

export interface LegibilityReport {
  ok: boolean;
  theme: string;
  width: number;
  height: number;
  scale: number;
  /** Smallest text size on the artboard, normalized to 1080 (null when no text). */
  minFontPx1080: number | null;
  layers: LayerLegibility[];
  /** Contrast of the roles used on this artboard against its background. */
  contrast: Array<ContrastCheck & { used: boolean }>;
  findings: LegibilityFinding[];
}

export interface LegibilityOptions {
  /** Datasets/sources to resolve numbers and citations (missing -> findings). */
  context?: LayoutContext;
  brand?: BrandTokens | null;
  theme?: ContentTheme;
  /** Override the theme's `min_font_px_1080` gate. */
  minFontPx1080?: number;
  minContrast?: number;
}

const round = (n: number) => Math.round(n * 100) / 100;

export function legibilityReport(artboard: ArtboardSpec, width = artboard.width, options: LegibilityOptions = {}): LegibilityReport {
  const theme = options.theme ?? resolveTheme(artboard.theme, options.brand ?? null);
  const ctx = options.context ?? EMPTY_CONTEXT;
  const minFont = options.minFontPx1080 ?? theme.legibility.minFontPx1080;
  const minContrast = options.minContrast ?? theme.legibility.minContrast;
  const layout = layoutArtboard(artboard, theme, ctx, width);
  const findings: LegibilityFinding[] = [];
  const layers: LayerLegibility[] = [];
  const usedRoles = new Set<TextRole>();
  let minFontPx1080: number | null = null;

  const safe = artboard.safe_area;
  for (const l of layout.layers) {
    const f = l.layer.frame;
    const eps = 1e-6;
    if (f.x + eps < safe.x || f.y + eps < safe.y || f.x + f.w > safe.x + safe.w + eps || f.y + f.h > safe.y + safe.h + eps) {
      findings.push({ code: "outside_safe_area", severity: "advisory", layer_id: l.layer.layer_id, message: `layer ${l.layer.layer_id} extends outside the safe area` });
    }
    if (l.kind === "text") {
      usedRoles.add(l.role);
      const px1080 = l.fit.fontSize / layout.scale;
      minFontPx1080 = minFontPx1080 === null ? px1080 : Math.min(minFontPx1080, px1080);
      layers.push({ layer_id: l.layer.layer_id, kind: l.layer.kind, role: l.role, fontPx: l.fit.fontSize, fontPx1080: round(px1080), lines: l.fit.lines.length, maxLines: l.maxLines, truncated: l.fit.truncated });
      if (px1080 < minFont) {
        findings.push({ code: "font_too_small", severity: "major", layer_id: l.layer.layer_id, message: `text fits only at ${round(px1080)}px (1080 basis); minimum is ${minFont}px`, value: round(px1080), threshold: minFont });
      }
      if (l.fit.truncated) {
        findings.push({ code: "truncated", severity: "blocker", layer_id: l.layer.layer_id, message: `text was cut with an ellipsis to fit ${l.maxLines} line(s)`, value: l.fit.lines.length, threshold: l.maxLines });
      }
      if (l.fit.lines.length > theme.legibility.maxLines) {
        findings.push({ code: "too_many_lines", severity: "advisory", layer_id: l.layer.layer_id, message: `${l.fit.lines.length} lines exceeds the editorial maximum of ${theme.legibility.maxLines}`, value: l.fit.lines.length, threshold: theme.legibility.maxLines });
      }
      for (const id of l.missingSources) {
        findings.push({ code: "missing_source", severity: "major", layer_id: l.layer.layer_id, message: `source ${id} is not in the bundle` });
      }
    } else if (l.kind === "number") {
      usedRoles.add("number");
      const px1080 = l.fit.fontSize / layout.scale;
      minFontPx1080 = minFontPx1080 === null ? px1080 : Math.min(minFontPx1080, px1080);
      layers.push({ layer_id: l.layer.layer_id, kind: "number", role: "number", fontPx: l.fit.fontSize, fontPx1080: round(px1080), lines: 1, maxLines: 1, truncated: l.fit.overflow });
      if (px1080 < minFont) {
        findings.push({ code: "font_too_small", severity: "major", layer_id: l.layer.layer_id, message: `number fits only at ${round(px1080)}px (1080 basis); minimum is ${minFont}px`, value: round(px1080), threshold: minFont });
      }
      if (l.fit.overflow) {
        findings.push({ code: "overflow", severity: "blocker", layer_id: l.layer.layer_id, message: "number does not fit its frame even at the minimum size" });
      }
      if (l.value === null) {
        findings.push({ code: "missing_data", severity: "major", layer_id: l.layer.layer_id, message: `value ${l.layer.value.dataset_id}/${l.layer.value.row_key ?? "*"}/${l.layer.value.column ?? "*"} did not resolve` });
      }
    } else {
      layers.push({ layer_id: l.layer.layer_id, kind: l.layer.kind, role: null, fontPx: null, fontPx1080: null, lines: null, maxLines: null, truncated: false });
    }
  }

  const bg = backgroundColor(theme, artboard.background_role);
  const contrast = contrastMatrix(theme)
    .filter((c) => c.background === artboard.background_role)
    .map((c) => ({ ...c, ratio: round(c.ratio), pass: c.ratio >= minContrast, used: usedRoles.has(c.role) }));
  for (const role of usedRoles) {
    const ratio = contrastRatio(textColor(theme, role, artboard.background_role), bg);
    if (ratio < minContrast) {
      findings.push({ code: "contrast_low", severity: "blocker", layer_id: null, message: `role ${role} on ${artboard.background_role} has contrast ${round(ratio)} < ${minContrast}`, value: round(ratio), threshold: minContrast });
    }
  }

  return {
    ok: findings.every((f) => f.severity === "advisory"),
    theme: theme.name,
    width: layout.width,
    height: layout.height,
    scale: layout.scale,
    minFontPx1080: minFontPx1080 === null ? null : round(minFontPx1080),
    layers,
    contrast,
    findings,
  };
}
