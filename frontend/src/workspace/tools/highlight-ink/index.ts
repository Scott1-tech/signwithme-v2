import { inkTool } from '../drawing';

/**
 * Freehand highlight (F, F2): press and drag -- a translucent marker stroke
 * (v7 `ink2` with `freehand_highlight`, opacity 0.35 unless set), yellow by
 * default, at least 8 pt wide.
 */
export default inkTool({
  id: 'highlight-ink',
  label: 'Freehand highlight',
  glyph: '🖍',
  hint: 'Mark freehand with a translucent highlighter: press and drag on the page',
  shortcut: 'F',
  group: 'annotate',
  order: 35,
  menu: 'annotate',
  menuSection: 'Freehand',
  highlighter: true,
});
