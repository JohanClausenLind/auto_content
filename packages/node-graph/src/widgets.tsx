/**
 * Widget rows inside a node.
 *
 * Each row is [connect dot] [label] [control] on one 20px-tall line, the geometry a node graph
 * uses. Controls are real form elements — a select, a number input, a textarea, a checkbox — so
 * they are keyboard reachable and screen-reader labelled; the pill styling sits on top of them
 * rather than replacing them with div soup.
 */

import { useEffect, useRef, useState, type ChangeEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { WidgetSpec, WidgetValue } from "./nodeDefs";

/** A plain triangle. Text glyphs like U+25C0 get hijacked by emoji fonts; an SVG never is. */
function Tri({ dir }: { dir: "left" | "right" }) {
  return (
    <svg className="ng-pill__tri" viewBox="0 0 6 8" aria-hidden="true" focusable="false">
      <path d={dir === "left" ? "M6 0 0 4 6 8Z" : "M0 0 6 4 0 8Z"} fill="currentColor" />
    </svg>
  );
}

export interface WidgetRowProps {
  readonly nodeId: string;
  readonly spec: WidgetSpec;
  readonly value: WidgetValue;
  readonly disabled?: boolean;
  onChange(value: WidgetValue): void;
}

function controlId(nodeId: string, name: string): string {
  return `ngw-${nodeId}-${name}`;
}

function clamp(value: number, spec: WidgetSpec): number {
  let next = value;
  if (spec.min !== undefined) next = Math.max(spec.min, next);
  if (spec.max !== undefined) next = Math.min(spec.max, next);
  return next;
}

function roundTo(value: number, spec: WidgetSpec): number {
  if (spec.kind === "int" || spec.kind === "seed") return Math.round(value);
  const precision = spec.precision ?? 2;
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
}

export function formatWidgetValue(spec: WidgetSpec, value: WidgetValue): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (spec.kind === "int" || spec.kind === "seed") return String(Math.round(value));
    return value.toFixed(spec.precision ?? 2).replace(/\.?0+$/, "");
  }
  return value;
}

function Label({ spec, htmlFor }: { spec: WidgetSpec; htmlFor: string }) {
  return (
    <label className="ng-widget__label" htmlFor={htmlFor} title={spec.help ?? spec.label ?? spec.name}>
      {spec.label ?? spec.name}
    </label>
  );
}

/** `◀ value ▶`: the arrows step through the options, the select opens the whole list. */
function ComboWidget({ nodeId, spec, value, disabled, onChange }: WidgetRowProps) {
  const id = controlId(nodeId, spec.name);
  const options = spec.options ?? [];
  const current = String(value);
  const index = options.indexOf(current);

  const step = (delta: number) => {
    if (options.length === 0) return;
    const from = index === -1 ? 0 : index;
    const next = options[(from + delta + options.length) % options.length];
    if (next !== undefined) onChange(next);
  };

  return (
    <div className="ng-widget__control ng-pill" data-kind="combo">
      <button
        type="button"
        className="ng-pill__arrow"
        aria-label={`Previous ${spec.label ?? spec.name}`}
        disabled={disabled || options.length < 2}
        onClick={() => step(-1)}
      >
        <Tri dir="left" />
      </button>
      <select
        id={id}
        className="ng-pill__select"
        value={index === -1 ? "" : current}
        disabled={disabled}
        onChange={(event: ChangeEvent<HTMLSelectElement>) => onChange(event.target.value)}
      >
        {index === -1 && <option value="">{current || "—"}</option>}
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="ng-pill__arrow"
        aria-label={`Next ${spec.label ?? spec.name}`}
        disabled={disabled || options.length < 2}
        onClick={() => step(1)}
      >
        <Tri dir="right" />
      </button>
    </div>
  );
}

/** Number pill: arrows step, the input takes typed values, dragging the label scrubs. */
function NumberWidget({ nodeId, spec, value, disabled, onChange }: WidgetRowProps) {
  const id = controlId(nodeId, spec.name);
  const numeric = typeof value === "number" ? value : Number(value) || 0;
  const step = spec.step ?? (spec.kind === "int" || spec.kind === "seed" ? 1 : 0.1);
  const [draft, setDraft] = useState<string | null>(null);
  const drag = useRef<{ startX: number; startValue: number } | null>(null);

  const emit = (next: number) => onChange(clamp(roundTo(next, spec), spec));

  const onPointerDown = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (disabled || event.button !== 0) return;
    drag.current = { startX: event.clientX, startValue: numeric };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLSpanElement>) => {
    const state = drag.current;
    if (!state) return;
    const delta = (event.clientX - state.startX) * step;
    if (delta !== 0) emit(state.startValue + delta);
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLSpanElement>) => {
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div className="ng-widget__control ng-pill" data-kind="number">
      <button
        type="button"
        className="ng-pill__arrow"
        aria-label={`Decrease ${spec.label ?? spec.name}`}
        disabled={disabled}
        onClick={() => emit(numeric - step)}
      >
        <Tri dir="left" />
      </button>
      <span
        className="ng-pill__scrub"
        aria-hidden="true"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
      <input
        id={id}
        className="ng-pill__number"
        type="number"
        inputMode={spec.kind === "float" ? "decimal" : "numeric"}
        value={draft ?? formatWidgetValue(spec, numeric)}
        step={step}
        {...(spec.min === undefined ? {} : { min: spec.min })}
        {...(spec.max === undefined ? {} : { max: spec.max })}
        disabled={disabled}
        onChange={(event) => {
          setDraft(event.target.value);
          const parsed = Number(event.target.value);
          if (event.target.value !== "" && Number.isFinite(parsed)) emit(parsed);
        }}
        onBlur={() => setDraft(null)}
      />
      <button
        type="button"
        className="ng-pill__arrow"
        aria-label={`Increase ${spec.label ?? spec.name}`}
        disabled={disabled}
        onClick={() => emit(numeric + step)}
      >
        <Tri dir="right" />
      </button>
    </div>
  );
}

const MAX_SEED = 0xff_ff_ff_ff;

/** A seed is a number plus a way to get a fresh one, because that is what it is for. */
function SeedWidget(props: WidgetRowProps) {
  return (
    <div className="ng-widget__control ng-seed">
      <NumberWidget {...props} />
      <button
        type="button"
        className="ng-seed__dice"
        aria-label={`Randomize ${props.spec.label ?? props.spec.name}`}
        disabled={props.disabled}
        onClick={() => props.onChange(Math.floor(Math.random() * MAX_SEED))}
      >
        <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false" className="ng-seed__icon">
          <path
            d="M6 1.5a4.5 4.5 0 1 0 4.5 4.5h-1.3A3.2 3.2 0 1 1 6 2.8V5l3-2.2L6-.4Z"
            fill="currentColor"
          />
        </svg>
      </button>
    </div>
  );
}

function TextWidget({ nodeId, spec, value, disabled, onChange }: WidgetRowProps) {
  return (
    <div className="ng-widget__control ng-pill" data-kind="text">
      <input
        id={controlId(nodeId, spec.name)}
        className="ng-pill__text"
        type="text"
        value={String(value)}
        placeholder={spec.placeholder ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function ToggleWidget({ nodeId, spec, value, disabled, onChange }: WidgetRowProps) {
  return (
    <div className="ng-widget__control ng-toggle">
      <input
        id={controlId(nodeId, spec.name)}
        className="ng-toggle__input"
        type="checkbox"
        checked={value === true}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="ng-toggle__track" aria-hidden="true" />
    </div>
  );
}

/** Keeps a textarea exactly as tall as its content (never below `minHeight`). */
export function useAutoGrowTextarea(value: string, minHeight = 48) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.max(element.scrollHeight, minHeight)}px`;
  }, [value, minHeight]);
  return ref;
}

/** The big prompt box. It grows with its content the way a text node should. */
function TextareaWidget({ nodeId, spec, value, disabled, onChange }: WidgetRowProps) {
  const text = String(value);
  const ref = useAutoGrowTextarea(text);
  return (
    <textarea
      ref={ref}
      id={controlId(nodeId, spec.name)}
      className="ng-textarea"
      rows={spec.rows ?? 4}
      value={text}
      placeholder={spec.placeholder ?? ""}
      disabled={disabled}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

/** True when the widget owns the whole node width instead of sharing a label row. */
export function isFullWidthWidget(spec: WidgetSpec): boolean {
  return spec.kind === "textarea";
}

export function WidgetRow(props: WidgetRowProps) {
  const { spec, nodeId } = props;
  const id = controlId(nodeId, spec.name);

  if (isFullWidthWidget(spec)) {
    return (
      <div className="ng-widget ng-widget--full" data-widget={spec.name}>
        <Label spec={spec} htmlFor={id} />
        <TextareaWidget {...props} />
      </div>
    );
  }

  const control =
    spec.kind === "combo" ? (
      <ComboWidget {...props} />
    ) : spec.kind === "toggle" ? (
      <ToggleWidget {...props} />
    ) : spec.kind === "text" ? (
      <TextWidget {...props} />
    ) : spec.kind === "seed" ? (
      <SeedWidget {...props} />
    ) : (
      <NumberWidget {...props} />
    );

  return (
    <div className="ng-widget" data-widget={spec.name}>
      <Label spec={spec} htmlFor={id} />
      {control}
    </div>
  );
}
