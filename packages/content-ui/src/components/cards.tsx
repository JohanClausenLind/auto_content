// Thin wrappers that build an ArtboardSpec + RenderBundle and render it through <Artboard>.
import type { ArtboardSpec, BrandTokens, DatasetTable, NumberLayer, RenderBundle, SourceCard, SourceLayer } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";

import { Artboard } from "./Artboard";

const NO_BRAND: BrandTokens = { accent: null, font_family: "Inter", ink: null, logo_asset_id: null, paper: null };

export interface CardBaseProps {
  width?: number;
  height?: number;
  background?: ArtboardSpec["background_role"];
  brand?: BrandTokens;
  theme?: string;
  sources?: SourceCard[];
  alt_text?: string;
}

function bundleFor(artboard: ArtboardSpec, sources: SourceCard[], datasets: Record<string, DatasetTable>, brand: BrandTokens): RenderBundle {
  return {
    artboard,
    assets: {},
    brand,
    bundle_id: "bnd_card00000001",
    datasets,
    kind: "artboard",
    plan: null,
    schema_version: 1,
    seed: "card",
    sources: Object.fromEntries(sources.map((s) => [s.source_id, s])),
    timeline: null,
  };
}

export interface DataCardProps extends CardBaseProps {
  label: string;
  value: number;
  unit?: string;
  format?: NumberLayer["format"];
  headline: string;
}

/** Number-led data card: label, big figure, headline, citation. */
export function DataCard(props: DataCardProps): ReactElement {
  const width = props.width ?? 1080;
  const height = props.height ?? 1080;
  const sources = props.sources ?? [];
  const dataset: DatasetTable = {
    classification: "DERIVED_DATA",
    columns: ["key", "value"],
    dataset_id: "ds_card000000001",
    label: props.label,
    rows: [{ key: "value", value: props.value }],
    source_ids: sources.map((s) => s.source_id),
    unit: props.unit ?? "",
  };
  const hasSources = sources.length > 0;
  const artboard: ArtboardSpec = {
    alt_text: props.alt_text ?? `${props.label}: ${props.value}${props.unit ?? ""} ${props.headline}`,
    artboard_id: "art_datacard0001",
    background_role: props.background ?? "paper",
    brand_kit_id: null,
    deliverable_id: "dlv_card00000001",
    height,
    layers: [
      { align: "start", frame: { x: 0.08, y: 0.1, w: 0.84, h: 0.08 }, kind: "text", layer_id: "lay_cardlabel001", locked: false, max_lines: 2, reading_order: 0, role: "label", text: { claim_ids: [], text: props.label } },
      { format: props.format ?? "auto", frame: { x: 0.08, y: 0.26, w: 0.84, h: 0.3 }, kind: "number", layer_id: "lay_cardnumber01", locked: false, reading_order: 1, unit: props.unit ?? "", value: { claim_id: null, column: "value", dataset_id: dataset.dataset_id, row_key: "value" } },
      { align: "start", frame: { x: 0.08, y: 0.58, w: 0.84, h: 0.24 }, kind: "text", layer_id: "lay_cardhead0001", locked: false, max_lines: 3, reading_order: 2, role: "headline", text: { claim_ids: [], text: props.headline } },
      ...(hasSources
        ? [{ frame: { x: 0.08, y: 0.86, w: 0.84, h: 0.06 }, kind: "source" as const, layer_id: "lay_cardsource01", locked: false, reading_order: 3, source_ids: sources.slice(0, 6).map((s) => s.source_id) as SourceLayer["source_ids"] }]
        : []),
    ],
    safe_area: { x: 0.06, y: 0.06, w: 0.88, h: 0.88 },
    schema_version: 1,
    theme: props.theme ?? "editorial",
    width,
  };
  return <Artboard bundle={bundleFor(artboard, sources, { [dataset.dataset_id]: dataset }, props.brand ?? NO_BRAND)} />;
}

export interface QuoteCardProps extends CardBaseProps {
  quote: string;
  attribution: string;
}

/** Pull-quote card: quote as headline, attribution as caption, citation. */
export function QuoteCard(props: QuoteCardProps): ReactElement {
  const width = props.width ?? 1080;
  const height = props.height ?? 1080;
  const sources = props.sources ?? [];
  const artboard: ArtboardSpec = {
    alt_text: props.alt_text ?? `Quote: “${props.quote}” — ${props.attribution}`,
    artboard_id: "art_quotecard001",
    background_role: props.background ?? "ink",
    brand_kit_id: null,
    deliverable_id: "dlv_card00000001",
    height,
    layers: [
      { color_role: "accent", frame: { x: 0.08, y: 0.14, w: 0.08, h: 0.006 }, kind: "shape", layer_id: "lay_quoterule001", locked: false, radius_token: "none", reading_order: 0, shape: "rule" },
      { align: "start", frame: { x: 0.08, y: 0.2, w: 0.84, h: 0.5 }, kind: "text", layer_id: "lay_quotetext001", locked: false, max_lines: 6, reading_order: 1, role: "headline", text: { claim_ids: [], text: `“${props.quote}”` } },
      { align: "start", frame: { x: 0.08, y: 0.74, w: 0.84, h: 0.08 }, kind: "text", layer_id: "lay_quoteattr001", locked: false, max_lines: 2, reading_order: 2, role: "caption", text: { claim_ids: [], text: `— ${props.attribution}` } },
      ...(sources.length > 0
        ? [{ frame: { x: 0.08, y: 0.86, w: 0.84, h: 0.06 }, kind: "source" as const, layer_id: "lay_quotesrc0001", locked: false, reading_order: 3, source_ids: sources.slice(0, 1).map((s) => s.source_id) as SourceLayer["source_ids"] }]
        : []),
    ],
    safe_area: { x: 0.06, y: 0.06, w: 0.88, h: 0.88 },
    schema_version: 1,
    theme: props.theme ?? "editorial",
    width,
  };
  return <Artboard bundle={bundleFor(artboard, sources, {}, props.brand ?? NO_BRAND)} />;
}
