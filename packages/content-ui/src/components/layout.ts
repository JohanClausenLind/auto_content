// Deterministic layout of an ArtboardSpec: one source of truth for the renderer and for QC.
import type {
  ArtboardSpec,
  DatasetTable,
  LayerSpec,
  NumberLayer,
  SourceCard,
  SourceLayer,
  TextLayer,
} from "@content-factory/content-schema-ts";

import { formatNumber, resolveNumber, type FormattedNumber } from "../format/number";
import { fitNumber, fitText, type FitNumberResult, type FitResult } from "../text/fit";
import {
  backgroundColor,
  scaleFor,
  textColor,
  type BackgroundRole,
  type ContentTheme,
  type TextRole,
  type TypeStyle,
} from "../tokens/theme";

/** Absolute floor so the fitter never overflows; QC flags anything below the theme minimum. */
export const HARD_MIN_PX_1080 = 8;

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface LayoutContext {
  datasets: Readonly<Record<string, DatasetTable>>;
  sources: Readonly<Record<string, SourceCard>>;
}

export interface TextLayout {
  kind: "text";
  layer: TextLayer | SourceLayer;
  box: Box;
  role: TextRole;
  style: TypeStyle;
  display: string;
  fit: FitResult;
  color: string;
  align: "start" | "center" | "end";
  maxLines: number;
  /** Source layers: ids that had no SourceCard in the bundle. */
  missingSources: string[];
}

export interface NumberLayout {
  kind: "number";
  layer: NumberLayer;
  box: Box;
  role: "number";
  style: TypeStyle;
  value: number | null;
  formatted: FormattedNumber;
  fit: FitNumberResult;
  color: string;
}

export interface OtherLayout {
  kind: "image" | "shape" | "chart";
  layer: LayerSpec;
  box: Box;
}

export type LayerLayout = TextLayout | NumberLayout | OtherLayout;

export interface ArtboardLayout {
  width: number;
  height: number;
  scale: number;
  background: BackgroundRole;
  backgroundColor: string;
  layers: LayerLayout[];
}

export const EMPTY_CONTEXT: LayoutContext = { datasets: {}, sources: {} };

export function frameToBox(frame: { x: number; y: number; w: number; h: number }, width: number, height: number): Box {
  const left = Math.round(frame.x * width);
  const top = Math.round(frame.y * height);
  return {
    left,
    top,
    width: Math.max(1, Math.round((frame.x + frame.w) * width) - left),
    height: Math.max(1, Math.round((frame.y + frame.h) * height) - top),
  };
}

export function citationText(sourceIds: readonly string[], sources: LayoutContext["sources"], theme: ContentTheme): {
  text: string;
  missing: string[];
} {
  const missing: string[] = [];
  const parts = sourceIds.map((id) => {
    const card = sources[id];
    if (!card) {
      missing.push(id);
      return `unknown source (${id})`;
    }
    const base = card.publisher.trim().length > 0 ? `${card.publisher}, ${card.title}` : card.title;
    return theme.citation.showAccessed ? `${base} (accessed ${card.accessed})` : base;
  });
  return { text: `${theme.citation.prefix}: ${parts.join(theme.citation.separator)}`, missing };
}

export function layoutArtboard(
  artboard: ArtboardSpec,
  theme: ContentTheme,
  ctx: LayoutContext = EMPTY_CONTEXT,
  width = artboard.width,
  height = Math.round((artboard.height * width) / artboard.width),
): ArtboardLayout {
  const scale = scaleFor(width, height);
  const background = artboard.background_role;
  const hardMin = HARD_MIN_PX_1080 * scale;
  const layers: LayerLayout[] = artboard.layers.map((layer): LayerLayout => {
    const box = frameToBox(layer.frame, width, height);
    switch (layer.kind) {
      case "text": {
        const style = theme.type[layer.role];
        const display = style.uppercase ? layer.text.text.toUpperCase() : layer.text.text;
        return {
          kind: "text",
          layer,
          box,
          role: layer.role,
          style,
          display,
          fit: fitText({
            text: display,
            weight: style.weight,
            maxWidth: box.width,
            maxHeight: box.height,
            maxLines: layer.max_lines,
            preferredSize: style.size * scale,
            minSize: hardMin,
            lineHeight: style.lineHeight,
            letterSpacing: style.letterSpacing,
          }),
          color: textColor(theme, layer.role, background),
          align: layer.align,
          maxLines: layer.max_lines,
          missingSources: [],
        };
      }
      case "source": {
        const style = theme.type[theme.citation.role];
        const { text, missing } = citationText(layer.source_ids, ctx.sources, theme);
        return {
          kind: "text",
          layer,
          box,
          role: theme.citation.role,
          style,
          display: text,
          fit: fitText({
            text,
            weight: style.weight,
            maxWidth: box.width,
            maxHeight: box.height,
            maxLines: 2,
            preferredSize: style.size * scale,
            minSize: hardMin,
            lineHeight: style.lineHeight,
            letterSpacing: style.letterSpacing,
          }),
          color: textColor(theme, theme.citation.role, background),
          align: "start",
          maxLines: 2,
          missingSources: missing,
        };
      }
      case "number": {
        const style = theme.type.number;
        const value = resolveNumber(layer.value, ctx.datasets);
        const formatted = formatNumber(value, layer.format, layer.unit);
        return {
          kind: "number",
          layer,
          box,
          role: "number",
          style,
          value,
          formatted,
          fit: fitNumber({
            numeral: formatted.prefix + formatted.numeral,
            unit: formatted.unit,
            weight: style.weight,
            maxWidth: box.width,
            maxHeight: box.height,
            preferredSize: style.size * scale,
            minSize: hardMin,
            lineHeight: style.lineHeight,
            letterSpacing: style.letterSpacing,
          }),
          color: textColor(theme, "number", background),
        };
      }
      default:
        return { kind: layer.kind, layer, box };
    }
  });
  return { width, height, scale, background, backgroundColor: backgroundColor(theme, background), layers };
}
