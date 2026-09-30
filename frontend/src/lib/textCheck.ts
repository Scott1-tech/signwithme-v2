import { EMBEDDED_RANGES, WINANSI_RANGES } from './fontCoverage';
import {
  FONT_LABEL,
  LIBERATION_OF,
  embeddedFaceName,
  isEmbeddedFamily,
  type FontFamily,
} from './fonts';

/**
 * Will the server print this text in this face? Asked BEFORE anything is
 * committed, so the user hears about a character the PDF cannot carry while
 * the text box is still open -- not from a 422 at finalize.
 *
 * The answer is the backend's own (`app/services/pdf_builder.py
 * undrawable_character`): the ranges in `fontCoverage.ts` are generated from
 * it (`frontend/scripts/gen_font_coverage.py`). Text is normalised to NFC
 * first -- the precomposed `é` prints, `e` + U+0301 does not -- and the
 * position is counted in code points, like the server's.
 */

export interface Undrawable {
  /** Position in code points (what the server reports). */
  index: number;
  char: string;
  codePoint: number;
  /** Why, in the backend's own words. */
  reason: string;
}

const REASON_BASE14 = 'the standard PDF fonts cover Western European text only';
const REASON_NO_GLYPH = 'the font has no glyph for it';
const REASON_INVISIBLE = 'control, format and private-use characters are not printed';
const REASON_COMBINING = 'combining marks are not supported; use the precomposed (NFC) character';
const REASON_RTL = 'right-to-left text is not supported';

const INVISIBLE = /[\p{Cc}\p{Cf}\p{Co}\p{Cs}]/u;
const COMBINING = /[\p{Mn}\p{Me}]/u;
const RTL =
  /[\p{Script=Hebrew}\p{Script=Arabic}\p{Script=Syriac}\p{Script=Thaana}\p{Script=Nko}\p{Script=Samaritan}\p{Script=Mandaic}\p{Script=Adlam}]/u;

function covered(ranges: ReadonlyArray<readonly [number, number]>, codePoint: number): boolean {
  let low = 0;
  let high = ranges.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const [start, end] = ranges[middle];
    if (codePoint < start) high = middle - 1;
    else if (codePoint > end) low = middle + 1;
    else return true;
  }
  return false;
}

/** The canonical form every annotation text is stored and sent in. */
export const normalizeText = (text: string): string => text.normalize('NFC');

/**
 * Zero-width FORMAT characters the PDF text editor leaves out -- the server's
 * `pdf_engine.model.IGNORABLE`: U+00AD soft hyphen, U+200B zero width space,
 * U+200C/U+200D (non-)joiner, U+2060 word joiner, U+FEFF zero width no-break
 * space. Docusign packages carry U+200B around most text; the user never sees
 * them, so an edit of such a line must not fail on them. Every OTHER control /
 * format character is still refused (U+202E right-to-left override among them).
 * Only the in-place text editor strips them: annotation texts keep the stricter
 * rule (the final PDF's own check refuses them).
 */
const IGNORABLE_ALL = /[­​‌‍⁠﻿]/g;

export const isIgnorable = (char: string): boolean =>
  char === '­' || char === '​' || char === '‌' || char === '‍' || char === '⁠' || char === '﻿';

/** `text` without the ignorable characters (see above). */
export const stripIgnorable = (text: string): string => text.replace(IGNORABLE_ALL, '');

/** The first character `font` (with `bold` / `italic`) cannot print, or `null`. */
export function findUndrawable(
  text: string,
  font: FontFamily,
  bold = false,
  italic = false,
): Undrawable | null {
  const face = embeddedFaceName(font, bold, italic);
  const ranges = face ? EMBEDDED_RANGES[face] : WINANSI_RANGES;
  if (!ranges) return null;
  let index = 0;
  for (const char of normalizeText(text)) {
    const codePoint = char.codePointAt(0) ?? 0;
    if (!covered(ranges, codePoint)) {
      let reason = REASON_BASE14;
      if (face) {
        reason = INVISIBLE.test(char)
          ? REASON_INVISIBLE
          : COMBINING.test(char)
            ? REASON_COMBINING
            : RTL.test(char)
              ? REASON_RTL
              : REASON_NO_GLYPH;
      }
      return { index, char, codePoint, reason };
    }
    index += 1;
  }
  return null;
}

const styleName = (bold: boolean, italic: boolean) =>
  bold && italic ? ' Bold Italic' : bold ? ' Bold' : italic ? ' Italic' : '';

/**
 * The sentence the user reads, e.g.
 * `Helvetica cannot print "П" (U+041F) at position 0 — the standard PDF fonts
 * cover Western European text only. Switch the font to Liberation Sans, which
 * can print this text.`
 */
export function undrawableMessage(
  found: Undrawable,
  font: FontFamily,
  bold = false,
  italic = false,
  text?: string,
): string {
  const name = `${FONT_LABEL[font]}${isEmbeddedFamily(font) ? styleName(bold, italic) : ''}`;
  const shown = INVISIBLE.test(found.char) || COMBINING.test(found.char) ? '' : ` "${found.char}"`;
  const hex = found.codePoint.toString(16).toUpperCase().padStart(4, '0');
  let message = `${name} cannot print${shown} (U+${hex}) at position ${found.index} — ${found.reason}.`;
  if (!isEmbeddedFamily(font)) {
    const alternative = LIBERATION_OF[font];
    if (text !== undefined && !findUndrawable(text, alternative, bold, italic)) {
      message += ` Switch the font to ${FONT_LABEL[alternative]}, which can print this text.`;
    } else {
      message += ' Remove the character, or choose a Liberation font.';
    }
  } else {
    message += ' Remove or replace that character.';
  }
  return message;
}

/** `null` when the text prints, else the message (NFC first). */
export function checkPrintable(
  text: string,
  font: FontFamily,
  bold = false,
  italic = false,
): string | null {
  const found = findUndrawable(text, font, bold, italic);
  return found ? undrawableMessage(found, font, bold, italic, text) : null;
}
