import { shapeTool } from '../drawing';

/** Rectangle (R): drag a box; Shift = a square. Stroke, colour and a translucent fill from the PropertiesBar. */
export default shapeTool('rect', {
  label: 'Rectangle',
  glyph: '▭',
  hint: 'Drag to draw a rectangle (Shift: a square)',
  shortcut: 'R',
  order: 10,
  menu: 'shapes',
});
