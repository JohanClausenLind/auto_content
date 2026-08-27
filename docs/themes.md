# App themes

The operator app is themeable; this never affects rendered content (content design tokens live in
`packages/content-ui`, phase 2). Implementation: `packages/web-ui/src/theme`.

- Every UI colour/spacing/radius/shadow/font/scale is a CSS custom property on `:root`
  (`--cf-bg`, `--cf-fg`, `--cf-panel`, `--cf-border`, `--cf-accent`, `--cf-danger`,
  `--cf-success`, `--cf-warn`, `--cf-muted`, `--cf-radius-*`, `--cf-space-*`, `--cf-shadow-*`,
  `--cf-font-sans`, `--cf-font-mono`, `--cf-scale`, `--cf-duration`).
- A preset is five base tokens (bg, fg, panel, border, accent); eighteen derived tokens are computed
  (pattern adopted from Odysseus, MIT — pattern only). Presets: `dark`, `light`, `midnight`,
  `paper`, `high-contrast` (≥ 7:1), `forest`, `ocean`, `retrowave`, `terminal`. Every preset is
  unit-tested for WCAG AA contrast (≥ 4.5:1) on fg/bg, fg/panel, accent-fg/accent, muted/bg.
- Customizer (Settings › Themes): live token editing, up to 8 named custom themes, JSON
  import/export validated with zod, UI scale 0.85–1.3, reduced-transparency toggle.
- `prefers-color-scheme` picks the default preset until the operator chooses;
  `prefers-reduced-motion` sets `--cf-duration: 0ms`.
- Persistence: `localStorage` for instant paint via an inline boot script in `index.html`
  (`themeBootScript`), then sync to the account (`GET/PUT /v1/prefs/theme`) through a
  `ThemeSyncAdapter`; sync failures are non-fatal. Preferences are per account, not per workspace.
