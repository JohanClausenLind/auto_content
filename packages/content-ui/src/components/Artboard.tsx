import type { ArtboardSpec, ChartLayer, ImageLayer, RenderBundle, ShapeLayer } from "@content-factory/content-schema-ts";
import type { CSSProperties, ReactElement } from "react";

import { FONT_STACK } from "../fonts";
import { colorByRole, resolveTheme, type ContentTheme } from "../tokens/theme";
import { layoutArtboard, type Box, type LayerLayout, type NumberLayout, type TextLayout } from "./layout";

export type AssetUrlResolver = (assetId: string, path: string) => string;

export interface ArtboardProps {
  bundle: RenderBundle;
  /** Defaults to `bundle.artboard`; throws if neither is present. */
  artboard?: ArtboardSpec;
  /** Maps `bundle.assets[assetId]` (a local path) to a URL the host can serve. Identity by default. */
  assetUrl?: AssetUrlResolver;
  /** Override the theme (defaults to `artboard.theme` + `bundle.brand`). */
  theme?: ContentTheme;
  style?: CSSProperties;
}

const identity: AssetUrlResolver = (_id, path) => path;

function boxStyle(box: Box): CSSProperties {
  return { position: "absolute", left: box.left, top: box.top, width: box.width, height: box.height, overflow: "hidden" };
}

const TEXT_ALIGN: Record<TextLayout["align"], CSSProperties["textAlign"]> = {
  start: "left",
  center: "center",
  end: "right",
};

export function TextBlock({ layout }: { layout: TextLayout }): ReactElement {
  const { fit, style, color, box, layer } = layout;
  return (
    <div
      data-layer-id={layer.layer_id}
      data-layer-kind={layer.kind}
      data-font-size={fit.fontSize}
      data-lines={fit.lines.length}
      style={{
        ...boxStyle(box),
        fontFamily: FONT_STACK,
        fontSize: fit.fontSize,
        fontWeight: style.weight,
        lineHeight: `${fit.lineHeightPx}px`,
        letterSpacing: `${style.letterSpacing}em`,
        color,
        textAlign: TEXT_ALIGN[layout.align],
        whiteSpace: "pre",
        fontFeatureSettings: style.tabular ? '"tnum"' : undefined,
      }}
    >
      {fit.lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

export function NumberBlock({ layout }: { layout: NumberLayout }): ReactElement {
  const { fit, style, color, box, layer, formatted, value } = layout;
  const justify = { start: "flex-start", center: "center", end: "flex-end" } as const;
  return (
    <div
      data-layer-id={layer.layer_id}
      data-layer-kind="number"
      data-value={value === null ? undefined : String(value)}
      data-font-size={fit.fontSize}
      style={{
        ...boxStyle(box),
        display: "flex",
        alignItems: "baseline",
        justifyContent: justify.start,
        fontFamily: FONT_STACK,
        fontWeight: style.weight,
        lineHeight: `${fit.heightPx}px`,
        letterSpacing: `${style.letterSpacing}em`,
        color,
        whiteSpace: "pre",
        fontFeatureSettings: '"tnum"',
      }}
    >
      <span style={{ fontSize: fit.fontSize }}>{formatted.prefix + formatted.numeral}</span>
      {formatted.unit.length > 0 ? (
        <span style={{ fontSize: fit.unitFontSize, marginLeft: Math.round(fit.fontSize * 0.06), letterSpacing: 0 }}>
          {formatted.unit}
        </span>
      ) : null}
    </div>
  );
}

function ImageBlock({
  layer,
  box,
  bundle,
  assetUrl,
  theme,
  background,
}: {
  layer: ImageLayer;
  box: Box;
  bundle: RenderBundle;
  assetUrl: AssetUrlResolver;
  theme: ContentTheme;
  background: ArtboardSpec["background_role"];
}): ReactElement {
  const path = bundle.assets[layer.asset_id];
  if (path === undefined) {
    return <PlaceholderBox layerId={layer.layer_id} kind="image" box={box} label={`missing asset · ${layer.asset_id}`} theme={theme} background={background} />;
  }
  const src = assetUrl(layer.asset_id, path);
  const crop = layer.crop;
  const imgStyle: CSSProperties = crop
    ? {
        position: "absolute",
        width: Math.round(box.width / crop.w),
        height: Math.round(box.height / crop.h),
        left: -Math.round((crop.x * box.width) / crop.w),
        top: -Math.round((crop.y * box.height) / crop.h),
        objectFit: layer.fit,
      }
    : { width: box.width, height: box.height, objectFit: layer.fit, display: "block" };
  return (
    <div data-layer-id={layer.layer_id} data-layer-kind="image" style={boxStyle(box)}>
      <img src={src} alt={layer.alt_text} style={imgStyle} />
    </div>
  );
}

function ShapeBlock({ layer, box, theme, background }: { layer: ShapeLayer; box: Box; theme: ContentTheme; background: ArtboardSpec["background_role"] }): ReactElement {
  const color = colorByRole(theme, layer.color_role, background);
  const radius = theme.radius[layer.radius_token];
  if (layer.shape === "rule") {
    const thickness = Math.max(1, Math.min(box.height, Math.round(2 * (box.width / 1080) + 1)));
    return (
      <div data-layer-id={layer.layer_id} data-layer-kind="shape" style={boxStyle(box)}>
        <div style={{ position: "absolute", left: 0, right: 0, top: Math.round((box.height - thickness) / 2), height: thickness, background: color }} />
      </div>
    );
  }
  return (
    <div
      data-layer-id={layer.layer_id}
      data-layer-kind="shape"
      style={{ ...boxStyle(box), background: color, borderRadius: layer.shape === "pill" ? theme.radius.full : radius }}
    />
  );
}

function PlaceholderBox({
  layerId,
  kind,
  box,
  label,
  theme,
  background,
}: {
  layerId: string;
  kind: string;
  box: Box;
  label: string;
  theme: ContentTheme;
  background: ArtboardSpec["background_role"];
}): ReactElement {
  const dark = background === "ink";
  const labelStyle = theme.type.label;
  const size = Math.max(12, Math.min(labelStyle.size, Math.floor(box.height / 2)));
  return (
    <div
      data-layer-id={layerId}
      data-layer-kind={kind}
      data-placeholder="true"
      style={{
        ...boxStyle(box),
        boxSizing: "border-box",
        background: colorByRole(theme, "surface", background),
        border: `2px dashed ${colorByRole(theme, "rule", background)}`,
        borderRadius: theme.radius.sm,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: FONT_STACK,
        fontSize: size,
        fontWeight: labelStyle.weight,
        letterSpacing: `${labelStyle.letterSpacing}em`,
        textTransform: "uppercase",
        color: dark ? theme.color.onInk.muted : theme.color.muted,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </div>
  );
}

function ChartBlock({ layer, box, theme, background }: { layer: ChartLayer; box: Box; theme: ContentTheme; background: ArtboardSpec["background_role"] }): ReactElement {
  return <PlaceholderBox layerId={layer.layer_id} kind="chart" box={box} label={`chart · ${layer.scene_ref}`} theme={theme} background={background} />;
}

/**
 * Renders an ArtboardSpec at its native size with data resolved from the bundle. Purely
 * deterministic: inline styles only, no transitions, no randomness, no network.
 */
export function Artboard({ bundle, artboard = bundle.artboard ?? undefined, assetUrl = identity, theme, style }: ArtboardProps): ReactElement {
  if (!artboard) throw new Error("Artboard: bundle has no artboard and none was supplied");
  const resolved = theme ?? resolveTheme(artboard.theme, bundle.brand);
  const layout = layoutArtboard(artboard, resolved, { datasets: bundle.datasets, sources: bundle.sources });
  return (
    <div
      role="img"
      aria-label={artboard.alt_text}
      data-artboard-id={artboard.artboard_id}
      data-theme={resolved.name}
      style={{
        position: "relative",
        width: layout.width,
        height: layout.height,
        overflow: "hidden",
        background: layout.backgroundColor,
        color: resolved.color.ink,
        fontFamily: FONT_STACK,
        ...style,
      }}
    >
      {layout.layers.map((l: LayerLayout) => {
        switch (l.kind) {
          case "text":
            return <TextBlock key={l.layer.layer_id} layout={l} />;
          case "number":
            return <NumberBlock key={l.layer.layer_id} layout={l} />;
          case "image":
            return (
              <ImageBlock key={l.layer.layer_id} layer={l.layer as ImageLayer} box={l.box} bundle={bundle} assetUrl={assetUrl} theme={resolved} background={layout.background} />
            );
          case "shape":
            return <ShapeBlock key={l.layer.layer_id} layer={l.layer as ShapeLayer} box={l.box} theme={resolved} background={layout.background} />;
          case "chart":
            return <ChartBlock key={l.layer.layer_id} layer={l.layer as ChartLayer} box={l.box} theme={resolved} background={layout.background} />;
          default:
            return null;
        }
      })}
    </div>
  );
}
