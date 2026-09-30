import type { EditorFontFamilyId, FontOut } from '../api/document';

/**
 * F2 (EDITOR-V2-PLAN §7 «FontPicker», B2 `GET /api/fonts`): every family the
 * server can set NEW text in -- an edited block (`text.replace`) or a text
 * box (`textbox` markup) -- and how the browser previews it.
 *
 * The CSS family names are EXACTLY the API names (`styles/fonts.css`
 * declares them from `public/fonts/<family>/`, the very files the backend
 * embeds), so the preview is set in the face that prints. The metrics are the
 * files' own hhea ascender / descender (what PyMuPDF's `Font.ascender`
 * answers, the numbers the engine's text-box layout uses).
 */

export const EDITOR_FAMILIES: readonly EditorFontFamilyId[] = [
  'LiberationSans',
  'LiberationSerif',
  'LiberationMono',
  'Arimo',
  'Carlito',
  'Caladea',
  'DejaVuSans',
  'DejaVuSerif',
  'Amiri',
];

export const isEditorFamily = (value: unknown): value is EditorFontFamilyId =>
  typeof value === 'string' && (EDITOR_FAMILIES as readonly string[]).includes(value);

export const EDITOR_FONT_LABEL: Record<EditorFontFamilyId, string> = {
  LiberationSans: 'Liberation Sans',
  LiberationSerif: 'Liberation Serif',
  LiberationMono: 'Liberation Mono',
  Arimo: 'Arimo',
  Carlito: 'Carlito',
  Caladea: 'Caladea',
  DejaVuSans: 'DejaVu Sans',
  DejaVuSerif: 'DejaVu Serif',
  Amiri: 'Amiri',
};

const FALLBACK: Record<EditorFontFamilyId, string> = {
  LiberationSans: 'Arial, Helvetica, sans-serif',
  LiberationSerif: '"Times New Roman", Times, serif',
  LiberationMono: '"Courier New", Courier, monospace',
  Arimo: 'Arial, sans-serif',
  Carlito: 'Calibri, sans-serif',
  Caladea: 'Cambria, serif',
  DejaVuSans: 'Verdana, sans-serif',
  DejaVuSerif: 'Georgia, serif',
  Amiri: 'serif',
};

/** CSS `font-family` for the preview: the embedded face first, a fallback for the first instant. */
export const editorFontCss = (family: EditorFontFamilyId): string => `${family}, ${FALLBACK[family]}`;

/** hhea ascender / descender (em) of each family (regular face; the bold ones only differ for DejaVu Serif by 0.01). */
export const EDITOR_FONT_METRICS: Record<EditorFontFamilyId, { ascent: number; descent: number }> = {
  LiberationSans: { ascent: 0.9053, descent: 0.2119 },
  LiberationSerif: { ascent: 0.8911, descent: 0.2163 },
  LiberationMono: { ascent: 0.8325, descent: 0.3003 },
  Arimo: { ascent: 0.9053, descent: 0.2119 },
  Carlito: { ascent: 0.9521, descent: 0.2686 },
  Caladea: { ascent: 0.9, descent: 0.25 },
  DejaVuSans: { ascent: 0.9282, descent: 0.2358 },
  DejaVuSerif: { ascent: 0.9282, descent: 0.2358 },
  Amiri: { ascent: 1.124, descent: 0.634 },
};

/** A CSS `font` shorthand for one face. */
export const editorCssFont = (family: EditorFontFamilyId, bold: boolean, italic: boolean, sizePx: number) =>
  `${italic ? 'italic ' : ''}${bold ? '700' : '400'} ${sizePx}px ${family}`;

const requested = new Map<string, Promise<void>>();

/** Load one face now (a measurement taken in a fallback face would be wrong). Never rejects. */
export function ensureEditorFont(family: string, bold = false, italic = false): Promise<void> {
  if (typeof document === 'undefined' || !('fonts' in document)) return Promise.resolve();
  const key = `${italic ? 'italic ' : ''}${bold ? '700' : '400'} 16px ${family}`;
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

let context: CanvasRenderingContext2D | null = null;

/** Advance width in PDF points (no kerning: the engine advances by glyph widths). */
export function measureEditorText(
  text: string,
  family: EditorFontFamilyId,
  bold: boolean,
  italic: boolean,
  sizePt: number,
): number {
  if (typeof document === 'undefined') return 0;
  if (!context) context = document.createElement('canvas').getContext('2d');
  if (!context) return 0;
  context.font = editorCssFont(family, bold, italic, 100);
  if ('fontKerning' in context) context.fontKerning = 'none';
  return (context.measureText(text).width * sizePt) / 100;
}

/**
 * «Calibri → Carlito (substitute)»: the chip the font picker shows for a font
 * of `GET /api/fonts` that stands in for a common one.
 */
export function substituteChip(font: Pick<FontOut, 'label' | 'substitutes_for'>): string | null {
  if (!font.substitutes_for.length) return null;
  return `${font.substitutes_for.join(', ')} → ${font.label} (substitute)`;
}

/** The licence in words (the API's SPDX-ish id). */
export function licenceLabel(license: string): string {
  if (license === 'OFL-1.1') return 'SIL Open Font License 1.1';
  if (license === 'Bitstream-Vera') return 'Bitstream Vera licence';
  if (license === 'base14') return 'PDF standard font';
  return license;
}
