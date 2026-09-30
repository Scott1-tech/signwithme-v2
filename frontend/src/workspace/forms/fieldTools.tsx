import type { FieldType } from '../../api/forms';
import type { Gesture, Point, ToolContext, ToolDefinition, ToolId } from '../tools/types';

/**
 * F2b: the Forms ▾ tools that make interactive PDF form fields (EDITOR-V2-PLAN
 * §7 «Toolbar»: «Text field, Multiline, Dropdown, Radio, Checkbox, Signature
 * box; Detect form fields»), under the heading «Add new form fields».
 *
 * A tool folder (`tools/field-<type>/index.ts`) is one call of `fieldTool` here;
 * the registry finds it by itself. What a drag makes is an OP of the edit
 * session (`field.add`), not an annotation, so the gesture is handed to the
 * workspace's forms controller (`useForms`) through `formsBridge` -- the one
 * forms controller a mounted workspace has. Without it (no document editor)
 * the tools are not on the toolbar at all (`requiresEditor`).
 */

export interface FormsBridge {
  /** A press on the page with a field tool armed: the drag that draws the field (null = refused, said why). */
  beginDraw(type: FieldType, ctx: ToolContext, pt: Point): Gesture | null;
}

export const formsBridge: { current: FormsBridge | null } = { current: null };

export const FIELD_TOOL_TYPE: Partial<Record<ToolId, FieldType>> = {
  'field-text': 'text',
  'field-multiline': 'multiline',
  'field-dropdown': 'dropdown',
  'field-radio': 'radio',
  'field-checkbox': 'checkbox',
  'field-signature': 'signature',
};

/** Every forms tool (the six field types + Detect form fields). */
export const FORM_TOOL_IDS: readonly ToolId[] = [
  'field-text',
  'field-multiline',
  'field-dropdown',
  'field-radio',
  'field-checkbox',
  'field-signature',
  'field-detect',
];

export const FORMS_SECTION = 'Add new form fields';

interface Meta {
  id: ToolId;
  type: FieldType;
  label: string;
  glyph: string;
  hint: string;
  order: number;
}

export function fieldTool(meta: Meta): ToolDefinition {
  return {
    id: meta.id,
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    group: 'forms',
    order: meta.order,
    menu: 'forms',
    menuSection: FORMS_SECTION,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    requiresEditor: true,
    properties: [],
    beginGesture(ctx, pt) {
      const bridge = formsBridge.current;
      if (!bridge) {
        ctx.notify('Form fields need the server’s document editor, which is not available for this contract.');
        return null;
      }
      return bridge.beginDraw(meta.type, ctx, pt);
    },
  };
}

/** «Detect form fields»: arming it opens the suggestions panel for the pages on screen. */
export const detectTool: ToolDefinition = {
  id: 'field-detect',
  label: 'Detect form fields',
  glyph: '⌕',
  hint: 'Find the blanks, boxes and signature lines of the pages on screen and suggest form fields (nothing is added until you accept)',
  group: 'forms',
  order: 110,
  menu: 'forms',
  menuSection: FORMS_SECTION,
  cursor: 'default',
  input: 'none',
  placesOnClick: false,
  requiresEditor: true,
  properties: [],
};
