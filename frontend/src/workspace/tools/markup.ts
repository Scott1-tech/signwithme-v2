import {
  MAX_ANNOTATIONS,
  MAX_MARKUP_RECTS,
  MARKUP_DEFAULT_COLOR,
  makeMarkupAnnotation,
  type MarkupKind,
} from '../../lib/annotations';
import type { ToolDefinition, ToolGroup } from './types';

/**
 * The common body of the three markup tools (W3): highlight, underline,
 * strikeout. Not a tool folder (the registry only globs `./<id>/index.ts`),
 * so it is never on the toolbar itself; each folder is one call of
 * `markupTool` with its own name, glyph and letter.
 *
 * INPUT: a text selection. With the tool armed the page's own text is laid
 * over it (`text/PdfTextLayer.tsx`, spans only) and made selectable
 * (`workspace--text-select`); a drag -- or a double-click on a word -- is
 * turned into one box per line in PDF points by `text/useTextSelection.ts`,
 * which hands each page's boxes here. The browser's selection is then
 * cleared: the mark replaces it.
 *
 * WHAT IS PLACED: one annotation per page, `rects` = the boxes (a selection
 * of more than `MAX_MARKUP_RECTS` lines on one page becomes several
 * annotations of ≤ 50 boxes each). Colour = the tool's (PropertiesBar);
 * default yellow for a highlight, red for the other two -- exactly the
 * server's defaults, but always SENT, so black is a colour too.
 *
 * On a page the reader has turned there is no text layer, so nothing can be
 * selected there: the tool is off on that page by construction.
 */
export function markupTool(
  kind: MarkupKind,
  meta: { label: string; glyph: string; hint: string; shortcut: string; order: number; group?: ToolGroup },
): ToolDefinition {
  return {
    id: kind,
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'annotate',
    order: meta.order,
    // F1a: the three open «Annotate ▾», under its «Text markup» heading.
    menu: 'annotate',
    menuSection: 'Text markup',
    cursor: 'text',
    input: 'text-selection',
    placesOnClick: false,
    needsTextLayer: true,
    properties: ['color'],
    defaultColor: MARKUP_DEFAULT_COLOR[kind],
    onTextSelection(ctx, rects) {
      if (rects.length === 0) return;
      const chunks: (typeof rects)[] = [];
      for (let start = 0; start < rects.length; start += MAX_MARKUP_RECTS) {
        chunks.push(rects.slice(start, start + MAX_MARKUP_RECTS));
      }
      if (ctx.annotations.items.length + chunks.length > MAX_ANNOTATIONS) {
        ctx.notify(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
        return;
      }
      chunks.forEach((boxes) => {
        const made = makeMarkupAnnotation(
          kind,
          { id: ctx.newId(), page: ctx.page, rects: boxes, color: ctx.props.color },
          ctx.pageSize,
        );
        if (made) ctx.annotations.add(made);
      });
    },
  };
}
