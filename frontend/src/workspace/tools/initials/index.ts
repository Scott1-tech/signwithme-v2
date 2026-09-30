import type { ToolDefinition } from '../types';

/** Why an initial is not in the undo history, said on the tool and after a placement. */
export const INITIALS_LOCK_NOTE =
  'Initials are stored on the server like a signature: Undo does not reach them — remove them with their own button.';

/**
 * Initials (I, W5, plan §7-7): a SMALL free signature. A click opens the
 * signature chooser (draw / upload / saved) for initials; the chosen image
 * is placed centred on the click, fitted inside 60 × 24 pt at its own ratio
 * (`lib/initials.ts`), through the unchanged free-signature endpoint
 * (`field_id: null`). So -- exactly like Signature -- it is a `serverWrite`
 * tool: off while signing is blocked (with the reason), and not in the undo
 * history. «Put on every page» / «Remove all initials» live in Objects.
 *
 * Unlike Signature, a click over a slot box lands on the PAGE (the slots step
 * aside): an initial is never a slot's signature.
 */
const initials: ToolDefinition = {
  id: 'initials',
  label: 'Initials',
  glyph: '✒',
  hint: `Click the page to place your initials there. ${INITIALS_LOCK_NOTE}`,
  shortcut: 'I',
  group: 'sign',
  order: 20,
  menu: 'sign',
  cursor: 'crosshair',
  input: 'click',
  placesOnClick: true,
  serverWrite: true,
  slotsStepAside: true,
  properties: [],
  onPageClick(ctx, pt) {
    if (ctx.gate.blocked) {
      ctx.notify(ctx.gate.reason ?? 'Initials can only be placed on a contract that can be signed.');
      return;
    }
    ctx.selection.selectAnnotation(null);
    ctx.notify(null);
    ctx.chooser.openInitials(ctx.page, pt);
  },
};

export default initials;
