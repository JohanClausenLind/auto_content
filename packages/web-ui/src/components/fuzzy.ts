/**
 * Subsequence fuzzy match. Returns a score (higher is better) or null when `query`
 * is not a subsequence of `text`. Word-start and consecutive hits score more.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const t = text.toLowerCase();
  let ti = 0;
  let score = 0;
  let streak = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const idx = t.indexOf(ch, ti);
    if (idx === -1) return null;
    const atWordStart = idx === 0 || /[\s\-_/.]/.test(t[idx - 1] ?? "");
    streak = idx === ti ? streak + 1 : 0;
    score += 1 + (atWordStart ? 3 : 0) + streak * 2 - Math.min(idx - ti, 5) * 0.1;
    ti = idx + 1;
  }
  if (t.startsWith(q)) score += 5;
  return score;
}

export function fuzzyMatches(query: string, text: string): boolean {
  return fuzzyScore(query, text) !== null;
}
