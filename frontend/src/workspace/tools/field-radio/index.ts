import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Radio» (a `field.add` of type `radio` in the edit session). */
export default fieldTool({
  id: 'field-radio',
  type: 'radio',
  label: 'Radio',
  glyph: '◉',
  hint: 'Click or drag on the page to add a radio button (one choice of a group)',
  order: 103,
});
