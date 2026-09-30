import { inkTool } from '../drawing';

/**
 * Freehand ink (P = pen): press and draw. One stroke = one ink annotation
 * (thinned by RDP at 0.5 pt, cut to the server's limits if it is very long),
 * one undo step. Round caps and joins, the stroke width and colour of the
 * PropertiesBar.
 */
export default inkTool({
  label: 'Draw',
  glyph: '✏',
  hint: 'Draw freehand: press and drag on the page',
  shortcut: 'P',
  group: 'annotate',
  order: 40,
  menu: 'annotate',
  menuSection: 'Freehand',
});
