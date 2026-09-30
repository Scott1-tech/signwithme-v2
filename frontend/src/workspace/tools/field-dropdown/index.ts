import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Dropdown» (a `field.add` of type `dropdown` in the edit session). */
export default fieldTool({
  id: 'field-dropdown',
  type: 'dropdown',
  label: 'Dropdown',
  glyph: '▾',
  hint: 'Drag on the page to draw a dropdown list of choices',
  order: 102,
});
