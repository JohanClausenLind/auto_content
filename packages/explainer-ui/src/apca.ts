// APCA-W3 0.0.98G-4g lightness contrast; the Python twin in explainer/color.py is the QC authority.
const NORM_BG = 0.56;
const NORM_TXT = 0.57;
const REV_TXT = 0.62;
const REV_BG = 0.65;
const BLK_THRS = 0.022;
const BLK_CLMP = 1.414;
const SCALE = 1.14;
const LO_OFFSET = 0.027;
const LO_CLIP = 0.1;
const DELTA_Y_MIN = 0.0005;

export function apcaLuminance([r, g, b]: readonly [number, number, number]): number {
  return 0.2126729 * (r / 255) ** 2.4 + 0.7151522 * (g / 255) ** 2.4 + 0.072175 * (b / 255) ** 2.4;
}

function softClamp(y: number): number {
  return y < BLK_THRS ? y + (BLK_THRS - y) ** BLK_CLMP : y;
}

/** Lc of text on background: positive dark-on-light, negative light-on-dark; |Lc| ≥ 75 reads as body text. */
export function apcaLc(text: readonly [number, number, number], background: readonly [number, number, number]): number {
  const yTxt = softClamp(apcaLuminance(text));
  const yBg = softClamp(apcaLuminance(background));
  if (Math.abs(yBg - yTxt) < DELTA_Y_MIN) return 0;
  if (yBg > yTxt) {
    const sapc = (yBg ** NORM_BG - yTxt ** NORM_TXT) * SCALE;
    return sapc < LO_CLIP ? 0 : (sapc - LO_OFFSET) * 100;
  }
  const sapc = (yBg ** REV_BG - yTxt ** REV_TXT) * SCALE;
  return sapc > -LO_CLIP ? 0 : (sapc + LO_OFFSET) * 100;
}
