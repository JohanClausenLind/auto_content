/**
 * Zero-import module so hosts (and Vite configs) can inline the boot script without
 * pulling in React. Keep it dependency-free.
 */
export const THEME_STORAGE_KEY = "cf.theme.v1";
export const THEME_PAINT_KEY = "cf.theme.paint.v1";

/**
 * Inline script for `index.html` (`<script>${themeBootScript}</script>`), run before the
 * app bundle so the first frame is already themed. It only reads the paint cache; it has
 * no knowledge of presets or derivation.
 */
export const themeBootScript: string = `(function(){try{var r=localStorage.getItem(${JSON.stringify(THEME_PAINT_KEY)});if(!r)return;var c=JSON.parse(r);var light=window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches;var p=light?c.light:c.dark;if(!p)return;var h=document.documentElement;for(var k in p.vars)h.style.setProperty(k,p.vars[k]);for(var a in p.attrs)h.setAttribute(a,p.attrs[a]);h.style.colorScheme=p.scheme;}catch(e){}})();`;
