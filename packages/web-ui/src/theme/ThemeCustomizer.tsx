import { useEffect, useMemo, useRef, useState } from "react";
import { Button as AriaButton, Group, Input, Label, Slider, SliderOutput, SliderThumb, SliderTrack, TextArea } from "react-aria-components";
import { Button } from "../components/Button";
import { Switch } from "../components/Switch";
import { TextField } from "../components/TextField";
import { contrastRatio, isHexColor } from "./color";
import type { ThemePreset } from "./presets";
import { MAX_CUSTOM_THEMES, SCALE_MAX, SCALE_MIN } from "./schema";
import { useTheme } from "./ThemeProvider";
import { BASE_TOKEN_KEYS, deriveTokens, type BaseTokens } from "./tokens";

const BASE_LABELS: Record<keyof BaseTokens, { label: string; hint: string }> = {
  bg: { label: "Background", hint: "The page behind everything." },
  fg: { label: "Text", hint: "Main text colour." },
  panel: { label: "Panels", hint: "Cards, sidebars, dialogs." },
  border: { label: "Borders", hint: "Dividers and outlines." },
  accent: { label: "Accent", hint: "Buttons, links, focus rings." },
};

function Swatch({ preset, selected, onSelect }: { preset: ThemePreset; selected: boolean; onSelect: () => void }) {
  const t = deriveTokens(preset.base, preset.advanced);
  return (
    <AriaButton
      onPress={onSelect}
      className="cf-swatch"
      aria-pressed={selected}
      aria-label={`${preset.name} theme. ${preset.description}`}
      style={{ background: t.bg, color: t.fg, borderColor: selected ? t.accent : t.border }}
    >
      <span className="cf-swatch__preview" style={{ background: t.panel, borderColor: t.border }}>
        <span className="cf-swatch__dot" style={{ background: t.accent }} />
        <span className="cf-swatch__line" style={{ background: t.fg }} />
        <span className="cf-swatch__line cf-swatch__line--muted" style={{ background: t.muted }} />
      </span>
      <span className="cf-swatch__name">{preset.name}</span>
    </AriaButton>
  );
}

function ContrastNote({ base }: { base: BaseTokens }) {
  const pairs: [string, number][] = [
    ["Text on background", contrastRatio(base.fg, base.bg)],
    ["Text on panels", contrastRatio(base.fg, base.panel)],
  ];
  return (
    <ul className="cf-contrast" aria-label="Contrast check">
      {pairs.map(([name, ratio]) => {
        const ok = ratio >= 4.5;
        return (
          <li key={name} className={ok ? "cf-contrast__ok" : "cf-contrast__low"}>
            <span>{name}</span>
            <span>
              {ratio.toFixed(1)}:1 {ok ? "· readable" : "· hard to read"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export interface ThemeCustomizerProps {
  /** Called after a save or when the operator is done. */
  onDone?: () => void;
}

/**
 * Progressive disclosure: presets first, then scale/transparency, then "Make your own"
 * (base tokens with live preview), then import/export.
 */
export function ThemeCustomizer({ onDone }: ThemeCustomizerProps) {
  const theme = useTheme();
  const { state, resolved } = theme;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<BaseTokens>(resolved.base);
  const [name, setName] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [importText, setImportText] = useState("");
  const [showImport, setShowImport] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const draftValid = useMemo(() => BASE_TOKEN_KEYS.every((k) => isHexColor(draft[k])), [draft]);

  // Live preview while editing; stop when leaving edit mode or unmounting.
  useEffect(() => {
    if (editing && draftValid) theme.preview({ base: draft, name: name || "Preview" });
    else theme.preview(null);
    return () => theme.preview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, draft, draftValid, name]);

  const startEditing = () => {
    setDraft(resolved.base);
    setName(state.mode === "custom" ? resolved.name : "");
    setEditing(true);
    setMessage(null);
  };

  const save = () => {
    const activeCustom = state.mode === "custom" ? state.customId : null;
    const sameName = activeCustom && state.customThemes.find((c) => c.id === activeCustom)?.name === name.trim();
    const result = theme.saveCustomTheme({ name, base: draft, ...(sameName ? { id: activeCustom } : {}) });
    if ("error" in result) {
      setMessage({ tone: "error", text: result.error });
      return;
    }
    setEditing(false);
    setMessage({ tone: "ok", text: `Saved “${result.name}”.` });
    onDone?.();
  };

  const doImport = (text: string) => {
    const result = theme.importJson(text);
    if (result.ok) {
      setMessage({ tone: "ok", text: result.kind === "state" ? "Imported your theme settings." : `Imported “${result.theme.name}”.` });
      setImportText("");
      setShowImport(false);
    } else {
      setMessage({ tone: "error", text: result.error });
    }
  };

  const exportJson = async () => {
    const json = theme.exportJson();
    try {
      await navigator.clipboard.writeText(json);
      setMessage({ tone: "ok", text: "Copied theme JSON to the clipboard." });
    } catch {
      setImportText(json);
      setShowImport(true);
      setMessage({ tone: "ok", text: "Clipboard unavailable — the JSON is in the box below." });
    }
  };

  const selectedPresetId = state.mode === "preset" ? state.preset : null;

  return (
    <div className="cf-customizer">
      <section className="cf-customizer__section" aria-labelledby="cf-presets-heading">
        <h3 id="cf-presets-heading" className="cf-customizer__heading">Theme</h3>
        <div className="cf-customizer__row">
          <Switch isSelected={state.mode === "system"} onChange={(on) => (on ? theme.followSystem() : theme.setPreset(resolved.id))}>
            Match my device
          </Switch>
        </div>
        <div className="cf-swatch-grid" role="group" aria-label="Presets">
          {theme.presets.filter((p) => !p.fun).map((p) => (
            <Swatch key={p.id} preset={p} selected={selectedPresetId === p.id} onSelect={() => theme.setPreset(p.id)} />
          ))}
        </div>
        <details className="cf-details">
          <summary>Fun presets</summary>
          <div className="cf-swatch-grid" role="group" aria-label="Fun presets">
            {theme.presets.filter((p) => p.fun).map((p) => (
              <Swatch key={p.id} preset={p} selected={selectedPresetId === p.id} onSelect={() => theme.setPreset(p.id)} />
            ))}
          </div>
        </details>
        {state.customThemes.length > 0 && (
          <div className="cf-custom-list" role="group" aria-label="Your themes">
            {state.customThemes.map((c) => (
              <div key={c.id} className="cf-custom-list__row">
                <AriaButton
                  className="cf-custom-list__pick"
                  aria-pressed={state.mode === "custom" && state.customId === c.id}
                  onPress={() => theme.activateCustomTheme(c.id)}
                >
                  <span className="cf-swatch__dot" style={{ background: c.base.accent }} aria-hidden="true" />
                  {c.name}
                </AriaButton>
                <Button variant="ghost" size="sm" aria-label={`Delete theme ${c.name}`} onPress={() => theme.deleteCustomTheme(c.id)}>
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="cf-customizer__section" aria-labelledby="cf-display-heading">
        <h3 id="cf-display-heading" className="cf-customizer__heading">Display</h3>
        <Slider className="cf-slider" value={state.scale} minValue={SCALE_MIN} maxValue={SCALE_MAX} step={0.05} onChange={(v) => theme.setScale(Array.isArray(v) ? (v[0] ?? 1) : v)}>
          <div className="cf-slider__head">
            <Label>Interface size</Label>
            <SliderOutput>{({ state: s }) => `${Math.round(s.getThumbValue(0) * 100)}%`}</SliderOutput>
          </div>
          <SliderTrack className="cf-slider__track">
            <SliderThumb className="cf-slider__thumb" aria-label="Interface size" />
          </SliderTrack>
        </Slider>
        <Switch isSelected={state.reducedTransparency} onChange={theme.setReducedTransparency} description="Use solid panels instead of frosted ones.">
          Reduce transparency
        </Switch>
      </section>

      <section className="cf-customizer__section" aria-labelledby="cf-own-heading">
        <h3 id="cf-own-heading" className="cf-customizer__heading">Make your own</h3>
        {!editing ? (
          <p className="cf-customizer__lead">
            Start from the current theme and change five colours; everything else follows.{" "}
            <Button variant="secondary" size="sm" onPress={startEditing} isDisabled={state.customThemes.length >= MAX_CUSTOM_THEMES && state.mode !== "custom"}>
              Customize colours
            </Button>
          </p>
        ) : (
          <div className="cf-editor">
            <TextField label="Theme name" value={name} onChange={setName} placeholder="e.g. Studio night" isRequired maxLength={40} />
            <div className="cf-editor__grid">
              {BASE_TOKEN_KEYS.map((key) => {
                const invalid = !isHexColor(draft[key]);
                const id = `cf-tok-${key}`;
                return (
                  <Group key={key} className="cf-editor__token" aria-labelledby={`${id}-label`}>
                    <Label id={`${id}-label`} className="cf-field__label">{BASE_LABELS[key].label}</Label>
                    <div className="cf-editor__inputs">
                      <input
                        type="color"
                        className="cf-editor__picker"
                        aria-label={`${BASE_LABELS[key].label} colour picker`}
                        value={invalid ? "#000000" : draft[key]}
                        onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                      />
                      <Input
                        className="cf-input cf-input--mono"
                        aria-label={`${BASE_LABELS[key].label} hex value`}
                        aria-invalid={invalid || undefined}
                        value={draft[key]}
                        onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                      />
                    </div>
                    <span className="cf-field__description">{BASE_LABELS[key].hint}</span>
                  </Group>
                );
              })}
            </div>
            {draftValid && <ContrastNote base={draft} />}
            <div className="cf-editor__actions">
              <Button variant="primary" onPress={save} isDisabled={!draftValid || !name.trim()}>
                Save theme
              </Button>
              <Button variant="ghost" onPress={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </section>

      <section className="cf-customizer__section" aria-labelledby="cf-share-heading">
        <h3 id="cf-share-heading" className="cf-customizer__heading">Import &amp; export</h3>
        <div className="cf-customizer__row">
          <Button variant="secondary" size="sm" onPress={() => void exportJson()}>
            Copy theme JSON
          </Button>
          <Button variant="secondary" size="sm" onPress={() => fileInput.current?.click()}>
            Import file…
          </Button>
          <Button variant="ghost" size="sm" onPress={() => setShowImport((s) => !s)} aria-expanded={showImport}>
            Paste JSON
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="cf-visually-hidden"
            aria-label="Theme file"
            tabIndex={-1}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              f.text().then(doImport, () => setMessage({ tone: "error", text: "Could not read that file." }));
              e.target.value = "";
            }}
          />
        </div>
        {showImport && (
          <div className="cf-import">
            <TextArea
              className="cf-input cf-input--mono cf-import__area"
              aria-label="Theme JSON"
              rows={6}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              spellCheck={false}
            />
            <Button variant="primary" size="sm" onPress={() => doImport(importText)} isDisabled={!importText.trim()}>
              Import
            </Button>
          </div>
        )}
      </section>

      <p role="status" aria-label="Theme status" aria-live="polite" className={["cf-customizer__status", message ? `cf-customizer__status--${message.tone}` : ""].join(" ").trim()}>
        {message?.text ?? ""}
      </p>
      {theme.syncError && (
        <p className="cf-text-muted cf-customizer__sync">Saved on this device. Server sync is unavailable right now.</p>
      )}
    </div>
  );
}
