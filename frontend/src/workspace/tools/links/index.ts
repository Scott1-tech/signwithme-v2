import { linkTool } from '../drawing';

/**
 * Links (F2, EDITOR-V2-PLAN §7.2): drag a box on the page and say where it
 * goes (a web / mail address, or a page of this document). Existing links of
 * the page are framed while the tool is armed: click one to edit, move,
 * resize or delete it. Clicking a link while editing never follows it. A
 * `link.add` / `link.update` / `link.remove` op of the edit session
 * (structure: the review stays).
 */
export default linkTool({
  label: 'Links',
  glyph: '🔗',
  hint: 'Drag a box over the page to make it a link (to a web address or a page); click a framed link to edit it',
  group: 'links',
  order: 10,
});
