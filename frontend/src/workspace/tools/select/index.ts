import type { ToolDefinition } from '../types';

/**
 * Select: press an annotation to select it, drag to move, the corner grip to
 * resize, double-click to retype. The page itself is not interactive in this
 * tool (a click on empty paper only lets go -- `Workspace.onStageClick`), so
 * `onPageClick` is only a safety net.
 */
const select: ToolDefinition = {
  id: 'select',
  label: 'Select',
  glyph: '⬉',
  hint: 'Select, move and resize',
  shortcut: 'V',
  group: 'select',
  order: 10,
  cursor: 'default',
  input: 'none',
  placesOnClick: false,
  properties: [],
  onPageClick(ctx) {
    ctx.selection.selectAnnotation(null);
  },
};

export default select;
