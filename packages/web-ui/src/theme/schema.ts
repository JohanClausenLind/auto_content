import { z } from "zod";
import { isHexColor } from "./color";
import { BASE_TOKEN_KEYS, DERIVED_TOKEN_KEYS } from "./tokens";

export const MAX_CUSTOM_THEMES = 8;
export const SCALE_MIN = 0.85;
export const SCALE_MAX = 1.3;

const hexColor = z.string().refine(isHexColor, "Expected a hex colour like #1a2b3c");

export const baseTokensSchema = z.object({
  bg: hexColor,
  fg: hexColor,
  panel: hexColor,
  border: hexColor,
  accent: hexColor,
});

const colorTokenKey = z.enum([...BASE_TOKEN_KEYS, ...DERIVED_TOKEN_KEYS]);

/** Advanced overrides accept any CSS colour string (shadows too), keyed by token. */
export const advancedTokensSchema = z.partialRecord(colorTokenKey, z.string().min(1).max(200));

export const customThemeSchema = z.object({
  id: z.string().min(1).max(64),
  name: z.string().trim().min(1).max(40),
  base: baseTokensSchema,
  advanced: advancedTokensSchema.optional(),
});

export const themeStateSchema = z.object({
  version: z.literal(1),
  /** "system" follows prefers-color-scheme; "preset"/"custom" pin a theme. */
  mode: z.enum(["system", "preset", "custom"]),
  preset: z.string().min(1).max(64),
  customId: z.string().min(1).max(64).nullable(),
  customThemes: z.array(customThemeSchema).max(MAX_CUSTOM_THEMES),
  scale: z.number().min(SCALE_MIN).max(SCALE_MAX),
  reducedTransparency: z.boolean(),
});

export type BaseTokensInput = z.infer<typeof baseTokensSchema>;
export type CustomTheme = z.infer<typeof customThemeSchema>;
export type ThemeState = z.infer<typeof themeStateSchema>;

/** Accepted by import: a whole state or a single custom theme. */
export const themeImportSchema = z.union([themeStateSchema, customThemeSchema]);

export const DEFAULT_THEME_STATE: ThemeState = {
  version: 1,
  mode: "system",
  preset: "dark",
  customId: null,
  customThemes: [],
  scale: 1,
  reducedTransparency: false,
};

export type ImportResult =
  | { ok: true; kind: "state"; state: ThemeState }
  | { ok: true; kind: "custom"; theme: CustomTheme }
  | { ok: false; error: string };

/** Parse JSON text into a validated import; never throws. */
export function parseThemeImport(text: string): ImportResult {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, error: "That is not valid JSON." };
  }
  const result = themeImportSchema.safeParse(data);
  if (!result.success) {
    const first = result.error.issues[0];
    const where = first?.path.length ? ` at ${first.path.join(".")}` : "";
    return { ok: false, error: `Theme file is not valid${where}: ${first?.message ?? "unknown"}` };
  }
  if ("version" in result.data) return { ok: true, kind: "state", state: result.data };
  return { ok: true, kind: "custom", theme: result.data };
}
