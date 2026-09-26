// Statement, big number, list, quotation card and formula: text set at role size, never shrunk.
// oxlint-disable-next-line typescript/triple-slash-reference -- the `*.css` ambient declaration must reach every program that imports this file
/// <reference path="../css.d.ts" />
import type { EntityBox, TextItem, TextTemplate as TextSpec } from "@content-factory/content-schema-ts";
import katex from "katex";
import type { CSSProperties, ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";
import { continueRender, delayRender } from "remotion";

import "katex/dist/katex.min.css";

import { useSceneEnv } from "../context";
import { entityBoxIndex, innerBlock, regionOf, safeAreaBox, type PixelBox } from "../geometry";
import { entityColors, mixHex, tokenHex } from "../palette";
import { entityOpacity, entityState, type SceneState } from "../state";
import { DISPLAY_STACK, LINE_HEIGHT, TEXT_STACK, lineHeightPx, rolePx, type TypeRole } from "../text";
import { TOKENS } from "../tokens.gen";
import type { TemplateProps } from "./props";

interface TextInk {
  primary: string;
  secondary: string;
  emphasis: string;
  surface1: string;
}

interface TextGeometry {
  sceneId: string;
  state: SceneState;
  boxes: ReadonlyMap<string, EntityBox>;
  colors: ReadonlyMap<string, string>;
  content: PixelBox;
  scale: number;
  unit: number;
  ink: TextInk;
}

/** The KaTeX faces a formula can reach for; loading them up front keeps every still identical. */
const KATEX_FACES = [
  "400 20px KaTeX_Main",
  "700 20px KaTeX_Main",
  "italic 400 20px KaTeX_Main",
  "italic 400 20px KaTeX_Math",
  "italic 700 20px KaTeX_Math",
  "400 20px KaTeX_Size1",
  "400 20px KaTeX_Size2",
  "400 20px KaTeX_Size3",
  "400 20px KaTeX_Size4",
  "400 20px KaTeX_AMS",
  "400 20px KaTeX_Caligraphic",
  "400 20px KaTeX_SansSerif",
  "400 20px KaTeX_Script",
  "400 20px KaTeX_Fraktur",
  "400 20px KaTeX_Typewriter",
];

function fontPxFor(g: TextGeometry, item: TextItem, role: TypeRole): number {
  return g.boxes.get(item.entity_id)?.font_px ?? rolePx(role, g.scale);
}

/** The compiler's verified text for the box when it set one, else the item's own. */
function textOf(g: TextGeometry, item: TextItem): string {
  return g.boxes.get(item.entity_id)?.text ?? item.text;
}

function inkFor(g: TextGeometry, entityId: string, base: string): string {
  return mixHex(base, g.ink.emphasis, entityState(g.state, entityId).highlight);
}

/** Absolute at the compiler's box when there is one, otherwise a block in the flow. */
function Placed({ g, item, lines, fontPx, style, children }: { g: TextGeometry; item: TextItem; lines: number; fontPx: number; style?: CSSProperties; children: ReactElement | string }): ReactElement {
  const box = g.boxes.get(item.entity_id);
  const base: CSSProperties = { lineHeight: `${lineHeightPx(fontPx)}px`, fontSize: fontPx, overflow: "hidden", opacity: entityOpacity(g.state, item.entity_id), ...style };
  if (box) {
    // Boxes are canvas pixels; the containing Flow sits at the content origin.
    const inner = innerBlock(box, fontPx, g.unit);
    return <div style={{ ...base, position: "absolute", left: inner.x - g.content.x, top: inner.y - g.content.y, width: inner.width, height: inner.height }}>{children}</div>;
  }
  return <div style={{ ...base, maxHeight: lineHeightPx(fontPx) * lines, width: "100%" }}>{children}</div>;
}

/** The quotation card around its items' compiled boxes: layout.py's CARD_INSET_* in units. */
function cardBox(g: TextGeometry, items: readonly TextItem[]): PixelBox | null {
  const boxes = items.map((item) => g.boxes.get(item.entity_id)?.box).filter((b): b is PixelBox => b !== undefined);
  if (boxes.length !== items.length) return null;
  const x = Math.min(...boxes.map((b) => b.x)) - 6 * g.unit;
  const y = Math.min(...boxes.map((b) => b.y)) - 4 * g.unit;
  const right = Math.max(...boxes.map((b) => b.x + b.width)) + 5 * g.unit;
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + 4 * g.unit;
  return { x, y, width: right - x, height: bottom - y };
}

function Flow({ g, gap, children }: { g: TextGeometry; gap: number; children: ReactElement[] | ReactElement }): ReactElement {
  return (
    <div style={{ position: "absolute", left: g.content.x, top: g.content.y, width: g.content.width, height: g.content.height, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "flex-start", gap, fontFamily: TEXT_STACK, color: g.ink.primary }}>
      {children}
    </div>
  );
}

function Statement({ g, items }: { g: TextGeometry; items: readonly TextItem[] }): ReactElement {
  return (
    <Flow g={g} gap={2 * g.unit}>
      {items.map((item) => (
        <Placed key={item.entity_id} g={g} item={item} lines={3} fontPx={fontPxFor(g, item, "h1")} style={{ fontWeight: 500, color: inkFor(g, item.entity_id, g.ink.primary), textAlign: "left" }}>
          {textOf(g, item)}
        </Placed>
      ))}
    </Flow>
  );
}

function BigNumber({ g, items }: { g: TextGeometry; items: readonly TextItem[] }): ReactElement {
  const env = useSceneEnv();
  const [number, label] = items;
  if (!number) return <Flow g={g} gap={0}>{[]}</Flow>;
  const caption = label ? textOf(g, label) : (env.entities.get(number.entity_id)?.label ?? "");
  return (
    <Flow g={g} gap={g.unit}>
      <Placed g={g} item={number} lines={1} fontPx={fontPxFor(g, number, "display")} style={{ fontFamily: DISPLAY_STACK, fontWeight: 700, color: inkFor(g, number.entity_id, g.ink.primary), whiteSpace: "nowrap" }}>
        {textOf(g, number)}
      </Placed>
      {label ? (
        <Placed g={g} item={label} lines={2} fontPx={fontPxFor(g, label, "body")} style={{ fontWeight: 500, color: inkFor(g, label.entity_id, g.ink.secondary) }}>
          {caption}
        </Placed>
      ) : (
        <div style={{ fontSize: rolePx("body", g.scale), lineHeight: `${lineHeightPx(rolePx("body", g.scale))}px`, fontWeight: 500, color: g.ink.secondary, opacity: entityOpacity(g.state, number.entity_id) }}>{caption}</div>
      )}
    </Flow>
  );
}

function List({ g, items }: { g: TextGeometry; items: readonly TextItem[] }): ReactElement {
  return (
    <Flow g={g} gap={2 * g.unit}>
      {items.map((item) => {
        const fontPx = fontPxFor(g, item, "body");
        return (
          <Placed key={item.entity_id} g={g} item={item} lines={2} fontPx={fontPx} style={{ fontWeight: 500, color: inkFor(g, item.entity_id, g.ink.primary) }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 2 * g.unit }}>
              <span style={{ flex: "0 0 auto", width: g.unit, height: g.unit, marginTop: (lineHeightPx(fontPx) - g.unit) / 2, background: g.colors.get(item.entity_id) ?? g.ink.secondary }} />
              <span>{textOf(g, item)}</span>
            </div>
          </Placed>
        );
      })}
    </Flow>
  );
}

function QuotationCard({ g, items }: { g: TextGeometry; items: readonly TextItem[] }): ReactElement {
  const [quote, attribution] = items;
  if (!quote) return <Flow g={g} gap={0}>{[]}</Flow>;
  const quotePx = fontPxFor(g, quote, "body");
  const chrome: CSSProperties = { boxSizing: "border-box", background: g.ink.surface1, borderRadius: 2 * g.unit, borderLeft: `${g.unit}px solid ${g.ink.emphasis}` };
  const body = (
    <>
      <Placed g={g} item={quote} lines={TOKENS.typography.max_lines.body} fontPx={quotePx} style={{ fontWeight: 500, color: inkFor(g, quote.entity_id, g.ink.primary) }}>
        {textOf(g, quote)}
      </Placed>
      {attribution ? (
        <Placed g={g} item={attribution} lines={1} fontPx={fontPxFor(g, attribution, "label")} style={{ fontWeight: 500, color: inkFor(g, attribution.entity_id, g.ink.secondary) }}>
          {`— ${textOf(g, attribution)}`}
        </Placed>
      ) : null}
    </>
  );
  const card = cardBox(g, items);
  if (card) {
    return (
      <Flow g={g} gap={0}>
        <div style={{ ...chrome, position: "absolute", left: card.x - g.content.x, top: card.y - g.content.y, width: card.width, height: card.height }} />
        {body}
      </Flow>
    );
  }
  return (
    <Flow g={g} gap={0}>
      <div style={{ ...chrome, width: "100%", padding: `${4 * g.unit}px ${5 * g.unit}px`, display: "flex", flexDirection: "column", gap: 2 * g.unit }}>{body}</div>
    </Flow>
  );
}

function useKatexFontsReady(): void {
  const [handle] = useState(() => delayRender("katex fonts"));
  useEffect(() => {
    const fonts = typeof document === "undefined" ? null : document.fonts;
    if (!fonts) {
      continueRender(handle);
      return;
    }
    Promise.all(KATEX_FACES.map((face) => fonts.load(face).catch(() => [])))
      .then(() => fonts.ready)
      .then(
        () => continueRender(handle),
        () => continueRender(handle),
      );
  }, [handle]);
}

/** The groups as one LaTeX string, each wrapped in a class the per-frame style block addresses. */
export function formulaLatex(items: readonly { entity_id: string; text: string }[]): string {
  return items.map((item) => `\\htmlClass{grp-${item.entity_id}}{${item.text}}`).join("\\,");
}

/** Per-frame opacity (visible × dim) and ink (emphasis as it highlights) of every group, scoped to the scene. */
function formulaStyle(g: TextGeometry, scope: string, items: readonly TextItem[]): string {
  const groups = items.map((item) => `.${scope} .grp-${item.entity_id}{opacity:${entityOpacity(g.state, item.entity_id)};color:${inkFor(g, item.entity_id, g.ink.primary)}}`);
  return [`.${scope} .katex-display{margin:0}`, ...groups].join("");
}

/** Typeset once at the compiler's shared box (centred in the template area), never wrapped. */
function Formula({ g, items }: { g: TextGeometry; items: readonly TextItem[] }): ReactElement {
  useKatexFontsReady();
  const latex = formulaLatex(items.map((item) => ({ entity_id: item.entity_id, text: textOf(g, item) })));
  const html = useMemo(
    () =>
      katex.renderToString(latex, {
        throwOnError: true,
        output: "html",
        displayMode: true,
        trust: (ctx) => ctx.command === "\\htmlClass",
        strict: (code) => (code === "htmlExtension" ? "ignore" : "warn"),
      }),
    [latex],
  );
  const first = items[0];
  const at = (first && g.boxes.get(first.entity_id)?.box) ?? g.content;
  const fontPx = first ? fontPxFor(g, first, "display") : rolePx("display", g.scale);
  const scope = `formula-${g.sceneId}`;
  return (
    <div className={scope} style={{ position: "absolute", left: at.x, top: at.y, width: at.width, height: at.height, display: "flex", alignItems: "center", justifyContent: "center", whiteSpace: "nowrap", fontSize: fontPx, lineHeight: LINE_HEIGHT, color: g.ink.primary }}>
      <style>{formulaStyle(g, scope, items)}</style>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

export function TextTemplate({ compiled, template, state }: TemplateProps<TextSpec>): ReactElement {
  const env = useSceneEnv();
  const { width, height } = env.bundle.timeline;
  const g: TextGeometry = {
    sceneId: compiled.scene_id,
    state,
    boxes: entityBoxIndex(compiled),
    colors: entityColors(compiled),
    content: regionOf(compiled, "content", "plot") ?? safeAreaBox(width, height),
    scale: env.scale,
    unit: TOKENS.layout.grid.unit * env.scale,
    ink: { primary: tokenHex("ui.ink.primary"), secondary: tokenHex("ui.ink.secondary"), emphasis: tokenHex("state.emphasis"), surface1: tokenHex("ui.surface.1") },
  };
  switch (template.variant) {
    case "big_number":
      return <BigNumber g={g} items={template.items} />;
    case "list":
      return <List g={g} items={template.items} />;
    case "quotation_card":
      return <QuotationCard g={g} items={template.items} />;
    case "formula":
      return <Formula g={g} items={template.items} />;
    default:
      return <Statement g={g} items={template.items} />;
  }
}
