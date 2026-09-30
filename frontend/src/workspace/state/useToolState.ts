import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ANNOTATION_FONTS,
  DEFAULT_COLOR,
  DEFAULT_FONT,
  DEFAULT_MARK_SIZE,
  DEFAULT_SIZE,
  DEFAULT_STROKE_PT,
  clampSize,
  clampStroke,
  isAnnotationColor,
  type AnnotationColor,
  type AnnotationFont,
} from '../../lib/annotations';
import { isRegisteredTool, toolOrSelect } from '../tools/registry';
import type { EditorFontFamilyId } from '../../api/document';
import type { StampSpec, ToolDefinition, ToolId, ToolProps, V7Props } from '../tools/types';

/** F2: the v7 look the next drawing starts with (the server's own defaults). */
export const V7_DEFAULTS: V7Props = {
  dash: 'solid',
  fillColor: null,
  opacity: 1,
  heads: 'end',
  align: 'left',
  lineHeight: 1.2,
};

/** F2: the stamp before «New Stamp» was ever used: APPROVED, red, bordered, no date. */
export const STAMP_DEFAULT: StampSpec = { preset: 'approved', text: null, date_text: null, color: 'red', border: true };

/**
 * The armed tool and what the NEXT annotation is placed with (plan §2.2).
 *
 * The defaults are kept per browser in `swm.toolProps` (font, the two sizes,
 * one colour per tool); the date is not -- it starts EMPTY every visit and the
 * Date tool places nothing until one is chosen (no automatic date, see
 * `lib/signing.ts`).
 * Anything stored that is not understood is dropped, key by key.
 */

export const TOOL_PROPS_KEY = 'swm.toolProps';

interface Stored {
  font: AnnotationFont;
  /** v6: the face of the next run / date (absent in an older blob = false). */
  bold: boolean;
  italic: boolean;
  size: number;
  markSize: number;
  /** W4: stroke width of the next ink / shape / line (absent in an older blob = 2 pt). */
  stroke: number;
  /** W4: the next rect / ellipse is filled (absent = false). */
  fill: boolean;
  colors: Partial<Record<ToolId, AnnotationColor>>;
}

const DEFAULTS: Stored = {
  font: DEFAULT_FONT,
  bold: false,
  italic: false,
  size: DEFAULT_SIZE,
  markSize: DEFAULT_MARK_SIZE,
  stroke: DEFAULT_STROKE_PT,
  fill: false,
  colors: {},
};

export function sanitizeToolProps(raw: unknown): Stored {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return DEFAULTS;
  const source = raw as Record<string, unknown>;
  const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
  const colors: Stored['colors'] = {};
  if (source.colors && typeof source.colors === 'object' && !Array.isArray(source.colors)) {
    Object.entries(source.colors as Record<string, unknown>).forEach(([tool, color]) => {
      if (isRegisteredTool(tool) && isAnnotationColor(color)) colors[tool] = color;
    });
  }
  const size = num(source.size);
  const markSize = num(source.markSize);
  const stroke = num(source.stroke);
  return {
    stroke: stroke === null ? DEFAULT_STROKE_PT : clampStroke(stroke),
    fill: source.fill === true,
    font: ANNOTATION_FONTS.includes(source.font as AnnotationFont)
      ? (source.font as AnnotationFont)
      : DEFAULT_FONT,
    bold: source.bold === true,
    italic: source.italic === true,
    size: size === null ? DEFAULT_SIZE : clampSize(size, 'text'),
    markSize: markSize === null ? DEFAULT_MARK_SIZE : clampSize(markSize, 'check'),
    colors,
  };
}

function load(): Stored {
  try {
    const raw = localStorage.getItem(TOOL_PROPS_KEY);
    return raw ? sanitizeToolProps(JSON.parse(raw) as unknown) : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

/** The colour a tool places with: the user's pick, else the tool's own default. */
export const colorForTool = (tool: ToolDefinition, colors: Stored['colors']): AnnotationColor =>
  colors[tool.id] ?? tool.defaultColor ?? DEFAULT_COLOR;

export function useToolState(initialTool: ToolId) {
  const [toolId, setToolId] = useState<ToolId>(() => toolOrSelect(initialTool).id);
  const [stored, setStored] = useState<Stored>(load);
  const [date, setDate] = useState<string>('');
  // F2: kept for this visit only (the stored blob of W2 is left as it was).
  const [v7, setV7State] = useState<V7Props>(V7_DEFAULTS);
  const [stamp, setStamp] = useState<StampSpec>(STAMP_DEFAULT);
  const [boxFont, setBoxFont] = useState<EditorFontFamilyId>('LiberationSans');

  useEffect(() => {
    try {
      localStorage.setItem(TOOL_PROPS_KEY, JSON.stringify(stored));
    } catch {
      /* private mode: the defaults simply do not persist */
    }
  }, [stored]);

  const tool = toolOrSelect(toolId);

  const props = useMemo<ToolProps>(
    () => ({
      font: stored.font,
      bold: stored.bold,
      italic: stored.italic,
      size: stored.size,
      markSize: stored.markSize,
      date,
      color: colorForTool(tool, stored.colors),
      stroke: stored.stroke,
      fill: stored.fill,
      v7,
      stamp,
      boxFont,
    }),
    [stored, date, tool, v7, stamp, boxFont],
  );

  const setFont = useCallback((font: AnnotationFont) => setStored((s) => ({ ...s, font })), []);
  const setStyle = useCallback(
    (style: { bold?: boolean; italic?: boolean }) =>
      setStored((s) => ({ ...s, bold: style.bold ?? s.bold, italic: style.italic ?? s.italic })),
    [],
  );
  const setSize = useCallback((size: number) => setStored((s) => ({ ...s, size })), []);
  const setMarkSize = useCallback((markSize: number) => setStored((s) => ({ ...s, markSize })), []);
  const setStroke = useCallback(
    (stroke: number) => setStored((s) => ({ ...s, stroke: clampStroke(stroke) })),
    [],
  );
  const setFill = useCallback((fill: boolean) => setStored((s) => ({ ...s, fill })), []);
  const setV7 = useCallback((change: Partial<V7Props>) => setV7State((current) => ({ ...current, ...change })), []);
  const setColorFor = useCallback(
    (id: ToolId, color: AnnotationColor) =>
      setStored((s) => ({ ...s, colors: { ...s.colors, [id]: color } })),
    [],
  );

  return {
    toolId,
    tool,
    setToolId,
    props,
    colors: stored.colors,
    setFont,
    setStyle,
    setSize,
    setMarkSize,
    setStroke,
    setFill,
    setDate,
    setColorFor,
    setV7,
    setStamp,
    setBoxFont,
  };
}

export type ToolState = ReturnType<typeof useToolState>;
