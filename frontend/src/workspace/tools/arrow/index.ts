import { lineTool } from '../drawing';

/** Arrow (A): like a line; the head is where the drag is let go. Shift = 45° steps. */
export default lineTool('arrow', {
  label: 'Arrow',
  glyph: '↗',
  hint: 'Drag to draw an arrow — the head is where you let go (Shift: 45° steps)',
  shortcut: 'A',
  order: 40,
  menu: 'shapes',
});
