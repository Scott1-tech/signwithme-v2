import { lineTool } from '../drawing';

/** Line (L): drag from one end to the other; Shift = 45° steps. */
export default lineTool('line', {
  label: 'Line',
  glyph: '╱',
  hint: 'Drag to draw a line (Shift: 45° steps)',
  shortcut: 'L',
  order: 30,
  menu: 'shapes',
});
