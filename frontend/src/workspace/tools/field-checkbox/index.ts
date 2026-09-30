import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Checkbox» (a `field.add` of type `checkbox` in the edit session). */
export default fieldTool({
  id: 'field-checkbox',
  type: 'checkbox',
  label: 'Checkbox',
  glyph: '☑',
  hint: 'Click or drag on the page to add a checkbox',
  order: 104,
});
