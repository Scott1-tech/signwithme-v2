import { shapeTool } from '../drawing';

/** Ellipse (O): drag its bounding box; Shift = a circle. */
export default shapeTool('ellipse', {
  label: 'Ellipse',
  glyph: '◯',
  hint: 'Drag to draw an ellipse (Shift: a circle)',
  shortcut: 'O',
  order: 20,
  menu: 'shapes',
});
