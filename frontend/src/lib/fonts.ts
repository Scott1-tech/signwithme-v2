import { useEffect, useState } from 'react';

/**
 * The six font FAMILIES a free annotation can be set in (contract v6), and
 * how the browser previews each one.
 *
 * * the standard 14 -- `Helvetica`, `Times-Roman`, `Courier`: not embedded in
 *   the PDF, WinAnsi only (Western European text);
 * * embedded TrueType -- `LiberationSans`, `LiberationSerif`, `LiberationMono`
 *   (SIL OFL 1.1, `public/fonts/liberation/`, the very files the backend
 *   subsets into the PDF): Cyrillic, Uzbek `oʻ gʻ` (U+02BB), `ş ç €` ...
 *
 * `bold` / `italic` pick one of four faces in every family.
 *
 * WHY THE PREVIEW ALWAYS DRAWS LIBERATION
 * ---------------------------------------
 * Liberation Sans / Serif / Mono are METRIC-compatible with Helvetica (Arial),
 * Times and Courier: every glyph has the same advance width (measured by the
 * backend: ≤ 0.017 %). So one set of files -- declared with `@font-face` in
 * `styles/fonts.css` under exactly these family names -- previews all six
 * families, and a line that fits on the screen fits in the PDF. Nothing here
 * depends on what the machine happens to have installed.
 */

export const FONT_FAMILIES = [
  'Helvetica',
  'Times-Roman',
  'Courier',
  'LiberationSans',
  'LiberationSerif',
  'LiberationMono',
] as const;
export type FontFamily = (typeof FONT_FAMILIES)[number];

export const isFontFamily = (value: unknown): value is FontFamily =>
  typeof value === 'string' && (FONT_FAMILIES as readonly string[]).includes(value);

export const FONT_LABEL: Record<FontFamily, string> = {
  Helvetica: 'Helvetica',
  'Times-Roman': 'Times',
  Courier: 'Courier',
  LiberationSans: 'Liberation Sans',
  LiberationSerif: 'Liberation Serif',
  LiberationMono: 'Liberation Mono',
};

/** The embedded family each family is drawn with (itself for the embedded three). */
export const LIBERATION_OF: Record<FontFamily, 'LiberationSans' | 'LiberationSerif' | 'LiberationMono'> = {
  Helvetica: 'LiberationSans',
  'Times-Roman': 'LiberationSerif',
  Courier: 'LiberationMono',
  LiberationSans: 'LiberationSans',
  LiberationSerif: 'LiberationSerif',
  LiberationMono: 'LiberationMono',
};

export const isEmbeddedFamily = (font: FontFamily): boolean => font.startsWith('Liberation');

/**
 * CSS `font-family` for the preview. The Liberation face comes first (loaded
 * by `@font-face`); the rest is only a fallback for the instant before it has
 * arrived -- and keeps the old family names in the string, which the checks
 * read (`contains(fontFamily, "Times")`).
 */
export const FONT_CSS: Record<FontFamily, string> = {
  Helvetica: 'LiberationSans, Helvetica, Arial, sans-serif',
  'Times-Roman': 'LiberationSerif, "Times New Roman", Times, serif',
  Courier: 'LiberationMono, "Courier New", Courier, monospace',
  LiberationSans: 'LiberationSans, Arial, Helvetica, sans-serif',
  LiberationSerif: 'LiberationSerif, "Times New Roman", Times, serif',
  LiberationMono: 'LiberationMono, "Courier New", Courier, monospace',
};

/** The backend's face name (`app/services/fonts.py resolve_font`) for an embedded family. */
export function embeddedFaceName(font: FontFamily, bold: boolean, italic: boolean): string | null {
  if (!isEmbeddedFamily(font)) return null;
  const style = bold && italic ? '-BoldItalic' : bold ? '-Bold' : italic ? '-Italic' : '';
  return `${font}${style}`;
}

/** A CSS `font` shorthand for one face at `sizePx`. */
export function cssFont(font: FontFamily, bold: boolean, italic: boolean, sizePx: number): string {
  return `${italic ? 'italic ' : ''}${bold ? '700' : '400'} ${sizePx}px ${LIBERATION_OF[font]}`;
}

/* -- loading ----------------------------------------------------------------- */

const requested = new Map<string, Promise<void>>();

/**
 * Ask the browser to load one face now. `@font-face` alone only loads a face
 * once something on screen uses it -- after the first measurement, which would
 * then be taken in a fallback font. Resolves (never rejects) when it is in.
 */
export function ensureFontLoaded(font: FontFamily, bold = false, italic = false): Promise<void> {
  if (typeof document === 'undefined' || !('fonts' in document)) return Promise.resolve();
  const key = cssFont(font, bold, italic, 16);
  let job = requested.get(key);
  if (!job) {
    job = document.fonts
      .load(key)
      .then(() => undefined)
      .catch(() => undefined);
    requested.set(key, job);
  }
  return job;
}

/**
 * A counter that moves every time the document finishes loading fonts. A
 * measurement that depends on a font (a run's `getBBox`, a width) lists it in
 * its effect's dependencies, so it is taken again once the real face is in.
 */
export function useFontEpoch(): number {
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    if (typeof document === 'undefined' || !('fonts' in document)) return undefined;
    const bump = () => setEpoch((value) => value + 1);
    document.fonts.addEventListener('loadingdone', bump);
    // Anything that finished before this mounted still has to count once.
    void document.fonts.ready.then(bump);
    return () => document.fonts.removeEventListener('loadingdone', bump);
  }, []);
  return epoch;
}

/* -- measuring ----------------------------------------------------------------- */

let measureContext: CanvasRenderingContext2D | null = null;

/**
 * The advance width of `text` in PDF points, set in that face at `sizePt`.
 * The same number the PDF builder gets (the faces are metric-compatible), as
 * long as the face is loaded -- see `ensureFontLoaded` / `useFontEpoch`.
 */
export function measureTextPt(
  text: string,
  font: FontFamily,
  bold: boolean,
  italic: boolean,
  sizePt: number,
): number {
  if (typeof document === 'undefined') return 0;
  if (!measureContext) measureContext = document.createElement('canvas').getContext('2d');
  if (!measureContext) return 0;
  // Measured at 100 px and scaled: small sizes are rounded by the canvas.
  measureContext.font = cssFont(font, bold, italic, 100);
  // No kerning: the PDF (reportlab, and pdf.js' item widths) advances by the
  // plain glyph widths; kerned "TOTAL" is ~0.5 pt narrower at 16 pt (W3).
  if ('fontKerning' in measureContext) measureContext.fontKerning = 'none';
  return (measureContext.measureText(text).width * sizePt) / 100;
}
