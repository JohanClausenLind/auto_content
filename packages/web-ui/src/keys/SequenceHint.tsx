/**
 * The "g …" indicator. Without it a sequence prefix looks like a dropped keypress: you press `g`,
 * nothing happens, and there is no way to tell the shell is waiting for the second chord.
 */

import { formatChord, isApplePlatform } from "./chord";
import { useOptionalKeymap } from "./KeymapProvider";

export function SequenceHint() {
  const keymap = useOptionalKeymap();
  const pending = keymap?.pending ?? [];
  if (pending.length === 0) return null;
  const apple = isApplePlatform();
  return (
    <div className="cf-seqhint" role="status" aria-live="polite">
      {pending.map((chord, i) => (
        <kbd key={`${chord}-${i}`} className="cf-kbd">
          {formatChord(chord, apple)}
        </kbd>
      ))}
      <span className="cf-seqhint__wait">…</span>
    </div>
  );
}
