import type { EditorFontFamilyId, Rect } from '../../api/document';
import {
  EXPORT_VALUE_RE,
  FIELD_NAME_RE,
  MAX_TAB_INDEX,
  isFieldOp,
  type DocumentOpB3,
  type FieldDef,
  type FieldOp,
  type FieldOut,
  type FieldType,
} from '../../api/forms';
import { newOpId } from '../session/useEditSession';

/**
 * F2b (EDITOR-V2-PLAN §3 `field.*`, §7.4): the form fields of the document as
 * the edit session sees them, and the ops that say so.
 *
 * ONE SOURCE OF TRUTH: the session's op list. What the page shows is always
 * `parse(base, ops)` -- the fields of the saved version (`GET …/fields`) with
 * the draft's field ops played over them -- and every user change goes the
 * other way: `parse` → change the records → `generate`, which writes the
 * field ops back in ONE canonical order:
 *
 *   field.remove  (fields of the version the user deleted)
 *   field.update  (fields of the version whose definition / place changed;
 *                  `name` = the name in the version, so a rename is one op)
 *   field.add     (fields made in this draft; moving one edits its add op)
 *   field.value   (one per field whose value the user set; last, so it
 *                  always names the field's FINAL name)
 *
 * The engine applies field ops in list order (`forms.apply_field_ops`), so
 * this order is valid whatever the user did in between (delete «Name», draw a
 * new «Name»; rename a field, then fill it). Op ids are kept per record, so an
 * undo / a redo / the autosave see the same ids for the same edit. A field
 * back at its saved definition has no op at all.
 */

export const FIELD_TYPES: readonly FieldType[] = ['text', 'multiline', 'dropdown', 'radio', 'checkbox', 'signature'];

export const FIELD_TYPE_LABEL: Record<FieldType | 'other', string> = {
  text: 'Text field',
  multiline: 'Multiline text',
  dropdown: 'Dropdown',
  radio: 'Radio button',
  checkbox: 'Checkbox',
  signature: 'Signature box',
  other: 'Unsupported field',
};

/** The name a new field of this type starts from (`Text_1`, `Text_2`, …). */
export const NAME_STEM: Record<FieldType, string> = {
  text: 'Text',
  multiline: 'Multiline',
  dropdown: 'Dropdown',
  radio: 'Radio',
  checkbox: 'Checkbox',
  signature: 'Signature',
};

/** A click (no drag) makes a field of this size, centred on the click (pt). */
export const CLICK_SIZE: Record<FieldType, { w: number; h: number }> = {
  text: { w: 160, h: 20 },
  multiline: { w: 220, h: 60 },
  dropdown: { w: 160, h: 20 },
  radio: { w: 14, h: 14 },
  checkbox: { w: 14, h: 14 },
  signature: { w: 180, h: 40 },
};

/** The families a widget can be drawn in (standard-14 faces: Helvetica / Times / Courier). */
export const FIELD_FONTS: ReadonlyArray<{ id: EditorFontFamilyId; label: string }> = [
  { id: 'LiberationSans', label: 'Sans (Helvetica)' },
  { id: 'LiberationSerif', label: 'Serif (Times)' },
  { id: 'LiberationMono', label: 'Mono (Courier)' },
];
const FONT_IDS = new Set<string>(['LiberationSans', 'LiberationSerif', 'LiberationMono', 'Arimo', 'Carlito',
  'Caladea', 'DejaVuSans', 'DejaVuSerif', 'Amiri']);

export const MIN_FIELD_PT = 4;
export const TEXT_TYPES: ReadonlySet<FieldType> = new Set(['text', 'multiline']);
export const FONT_TYPES: ReadonlySet<FieldType> = new Set(['text', 'multiline', 'dropdown']);

export type FieldValue = string | boolean | string[];

// -- WinAnsi (cp1252): what a widget can draw ------------------------------------------

const CP1252_EXTRA = new Set(
  Array.from('€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ').map((char) => char.codePointAt(0) as number),
);

/** The first character a form field cannot hold (not WinAnsi), or null. */
export function winAnsiProblem(text: string, multiline = false): string | null {
  for (const char of text) {
    const code = char.codePointAt(0) as number;
    if (char === '\n' && multiline) continue;
    if (code >= 0x20 && code <= 0x7e) continue;
    if (code >= 0xa0 && code <= 0xff) continue;
    if (CP1252_EXTRA.has(code)) continue;
    return char;
  }
  return null;
}

export function charsetMessage(char: string): string {
  const code = `U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}`;
  return (
    `“${char}” (${code}) cannot be written into a PDF form field: form fields hold Latin (Windows-1252) ` +
    'characters only — Cyrillic, emoji and similar are refused by the document. Nothing was sent.'
  );
}

// -- names -------------------------------------------------------------------------------

export const isValidName = (name: string) => FIELD_NAME_RE.test(name);
export const isValidExport = (value: string) => EXPORT_VALUE_RE.test(value);

/** A valid field name from anything (spaces → `_`, other characters dropped). */
export function sanitizeName(raw: string, fallback = 'Field'): string {
  const cleaned = raw.replace(/\s+/g, '_').replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 64);
  return cleaned || fallback;
}

/** `stem_1`, `stem_2`, … the first one not taken. */
export function freshName(stem: string, taken: ReadonlySet<string>): string {
  for (let n = 1; n < 100000; n += 1) {
    const name = `${stem}_${n}`.slice(0, 64);
    if (!taken.has(name)) return name;
  }
  return `${stem}_${Date.now()}`;
}

/** «Rename to X_2»: the name with the first free `_N` suffix (N ≥ 2). */
export function suggestRename(name: string, taken: ReadonlySet<string>): string {
  const stem = name;
  for (let n = 2; n < 100000; n += 1) {
    const suffix = `_${n}`;
    const candidate = stem.slice(0, 64 - suffix.length) + suffix;
    if (!taken.has(candidate)) return candidate;
  }
  return `${stem}_${Date.now()}`.slice(0, 64);
}

/** The first `ChoiceN` not used by the radio group. */
export function freshExportValue(taken: ReadonlySet<string>): string {
  for (let n = 1; n < 100000; n += 1) {
    if (!taken.has(`Choice${n}`)) return `Choice${n}`;
  }
  return `Choice${Date.now()}`;
}

// -- records -----------------------------------------------------------------------------

export interface FieldRecord {
  /** Stable while the page shows it: `o<index>` (a field of the version) / `n<op id>` (drawn in this draft). */
  key: string;
  /** The widget as the saved version has it (null = added in this draft). */
  origin: FieldOut | null;
  /** The definition now. */
  def: FieldDef;
  /** The definition the saved version has (null = added in this draft). */
  baseDef: FieldDef | null;
  pageUid: string;
  removed: boolean;
  /** The add / update / remove op that carries this record (null = untouched). */
  opId: string | null;
  /** The definition may be changed here (why not, in words). */
  lockedReason: string | null;
  /** The saved version's own value (what the field shows with no value op). */
  baseValue: FieldValue | null;
}

export interface FormState {
  records: FieldRecord[];
  /** The values the user set, by the field's (final) name. */
  values: Map<string, { value: FieldValue; opId: string }>;
}

const round2 = (value: number) => Math.round(value * 100) / 100;
export const rectOf = (rect: Rect): Rect => ({
  x_pt: round2(rect.x_pt),
  y_pt: round2(rect.y_pt),
  w_pt: round2(rect.w_pt),
  h_pt: round2(rect.h_pt),
});

/**
 * A definition with only the keys that say something, in one order: what is
 * sent, and what two definitions are compared by.
 */
export function cleanDef(def: FieldDef): FieldDef {
  const type = def.type;
  const out: FieldDef = { name: def.name, type, rect: rectOf(def.rect), tab_index: Math.max(0, Math.min(MAX_TAB_INDEX, Math.round(def.tab_index))) };
  if (def.required) out.required = true;
  if (def.readonly) out.readonly = true;
  if (def.tooltip) out.tooltip = def.tooltip;
  if (type === 'checkbox' || type === 'radio') {
    if (def.default === true) out.default = true;
  } else if (type !== 'signature' && typeof def.default === 'string' && def.default !== '') {
    if (type !== 'dropdown' || (def.options ?? []).includes(def.default)) out.default = def.default;
  }
  if (type === 'dropdown') out.options = [...(def.options ?? [])];
  if ((type === 'radio' || type === 'checkbox') && def.export_value) out.export_value = def.export_value;
  if (FONT_TYPES.has(type)) {
    if (def.font) out.font = def.font;
    if (def.size_pt) out.size_pt = def.size_pt;
  }
  if (TEXT_TYPES.has(type) && def.max_len) out.max_len = def.max_len;
  return out;
}

const sameDef = (a: FieldDef, b: FieldDef) => JSON.stringify(cleanDef(a)) === JSON.stringify(cleanDef(b));

/** The version's widget as a definition (`tab` = its place when the file has none). */
function defFromOut(out: FieldOut, tab: number): FieldDef {
  const type = (out.type === 'other' ? 'text' : out.type) as FieldType;
  return cleanDef({
    name: out.name,
    type,
    rect: out.rect,
    required: out.required,
    readonly: out.readonly,
    tooltip: out.tooltip,
    default: out.default,
    options: out.options,
    export_value: out.export_value,
    font: out.font && FONT_IDS.has(out.font) ? (out.font as EditorFontFamilyId) : null,
    size_pt: out.size_pt,
    max_len: out.max_len,
    tab_index: out.tab_index ?? tab,
  });
}

function lockedReasonOf(out: FieldOut, base: readonly FieldOut[]): string | null {
  if (out.type === 'other') return 'This kind of field (a button or a list) cannot be edited here.';
  if (!isValidName(out.name)) {
    return 'The document gave this field a name the editor cannot write back (letters, digits, _ . - only, ≤ 64).';
  }
  if (out.type === 'radio' && (!out.export_value || !isValidExport(out.export_value))) {
    return 'This radio button’s value cannot be written back by the editor.';
  }
  if (out.type !== 'radio' && base.filter((item) => item.name === out.name && !item.hidden).length > 1) {
    return 'This field has several boxes in the document; it can be filled, not reshaped.';
  }
  return null;
}

export function baseRecords(base: readonly FieldOut[]): FieldRecord[] {
  return base.map((out, index) => {
    const def = defFromOut(out, (index + 1) * 10);
    return {
    key: `o${index}`,
    origin: out,
    def,
    baseDef: def,
    pageUid: out.page_uid,
    removed: false,
    opId: null,
    lockedReason: lockedReasonOf(out, base),
    baseValue: out.value,
    };
  });
}

/** The state the version + these field ops describe. */
export function parse(base: readonly FieldOut[], ops: readonly FieldOp[]): FormState {
  const records = baseRecords(base);
  const values = new Map<string, { value: FieldValue; opId: string }>();
  const live = () => records.filter((record) => !record.removed);
  ops.forEach((op) => {
    if (op.kind === 'field.add') {
      records.push({
        key: `n${op.id}`,
        origin: null,
        def: cleanDef(op.field),
        baseDef: null,
        pageUid: op.page_uid,
        removed: false,
        opId: op.id,
        lockedReason: null,
        baseValue: null,
      });
    } else if (op.kind === 'field.update') {
      const current = op.name ?? op.field.name;
      const target = live().find(
        (record) =>
          record.def.name === current &&
          (op.export_value == null || record.def.export_value === op.export_value),
      );
      if (target) {
        target.def = cleanDef(op.field);
        target.pageUid = op.page_uid;
        target.opId = op.id;
      }
    } else if (op.kind === 'field.remove') {
      live()
        .filter(
          (record) =>
            record.def.name === op.name && (op.export_value == null || record.def.export_value === op.export_value),
        )
        .forEach((record) => {
          if (record.origin) {
            record.removed = true;
            record.opId = op.id;
          } else {
            records.splice(records.indexOf(record), 1);
          }
        });
    } else if (op.kind === 'field.value') {
      values.set(op.name, { value: op.value, opId: op.id });
    }
  });
  return { records, values };
}

export const fieldOpsOf = (list: readonly DocumentOpB3[]): FieldOp[] => list.filter(isFieldOp);

/** The field ops of this state, in the canonical order (see the header). */
export function generate(state: FormState): FieldOp[] {
  const removes: FieldOp[] = [];
  const updates: FieldOp[] = [];
  const adds: FieldOp[] = [];
  const removedNames = new Set<string>();
  state.records.forEach((record) => {
    const origin = record.origin;
    if (origin && record.removed) {
      const radio = origin.type === 'radio';
      if (!radio && removedNames.has(origin.name)) return;
      if (!radio) removedNames.add(origin.name);
      record.opId = record.opId ?? newOpId();
      removes.push({
        id: record.opId,
        kind: 'field.remove',
        name: origin.name,
        ...(radio && origin.export_value ? { export_value: origin.export_value } : {}),
      });
      return;
    }
    if (record.removed) return;
    if (origin) {
      const unchanged =
        record.pageUid === origin.page_uid && record.baseDef !== null && sameDef(record.def, record.baseDef);
      if (unchanged || record.lockedReason) {
        record.opId = null;
        return;
      }
      record.opId = record.opId ?? newOpId();
      updates.push({
        id: record.opId,
        kind: 'field.update',
        page_uid: record.pageUid,
        name: origin.name,
        ...(origin.type === 'radio' && origin.export_value ? { export_value: origin.export_value } : {}),
        field: cleanDef(record.def),
      });
      return;
    }
    record.opId = record.opId ?? newOpId();
    adds.push({ id: record.opId, kind: 'field.add', page_uid: record.pageUid, field: cleanDef(record.def) });
  });
  const liveNames = new Set(state.records.filter((record) => !record.removed).map((record) => record.def.name));
  const values: FieldOp[] = [];
  state.values.forEach((entry, name) => {
    if (!liveNames.has(name)) return;
    values.push({ id: entry.opId, kind: 'field.value', name, value: entry.value });
  });
  return [...removes, ...updates, ...adds, ...values];
}

/** The list with its field ops replaced by `fieldOps` (every other op kept, in order). */
export const withFieldOps = (list: readonly DocumentOpB3[], fieldOps: readonly FieldOp[]): DocumentOpB3[] => [
  ...list.filter((op) => !isFieldOp(op)),
  ...fieldOps,
];

// -- reading the state -------------------------------------------------------------------

export const liveRecords = (state: FormState) => state.records.filter((record) => !record.removed);

export const takenNames = (state: FormState, except?: FieldRecord) =>
  new Set(liveRecords(state).filter((record) => record !== except).map((record) => record.def.name));

/** The group a radio button belongs to (itself included). */
export const radioGroup = (state: FormState, name: string) =>
  liveRecords(state).filter((record) => record.def.type === 'radio' && record.def.name === name);

/**
 * Whether `name` may be given to `record`: '' ok, else why not. A radio
 * button may join a radio group of that name (its value must be new there).
 */
export function nameProblem(state: FormState, record: FieldRecord, name: string): 'invalid' | 'taken' | null {
  if (!isValidName(name)) return 'invalid';
  const others = liveRecords(state).filter((item) => item !== record && item.def.name === name);
  if (others.length === 0) return null;
  if (record.def.type === 'radio' && others.every((item) => item.def.type === 'radio')) return null;
  return 'taken';
}

/** The value the field shows now (the user's, else the version's, else the default). */
export function valueOf(state: FormState, record: FieldRecord): FieldValue | null {
  const set = state.values.get(record.def.name);
  if (set) return set.value;
  const type = record.def.type;
  if (type === 'radio') {
    const group = radioGroup(state, record.def.name);
    const saved = group.find((item) => typeof item.baseValue === 'string' && item.baseValue);
    if (saved) return saved.baseValue;
    const first = group[0];
    return first && first.def.default === true && !first.origin ? (first.def.export_value ?? '') : '';
  }
  if (record.origin) {
    if (record.origin.type === type) return record.baseValue;
    return null;
  }
  if (type === 'checkbox') return record.def.default === true;
  if (type === 'signature') return null;
  return typeof record.def.default === 'string' ? record.def.default : '';
}

/** Tab order: by page (as shown), then `tab_index`, then the order they came in. */
export function tabOrder(state: FormState, pageIndex: (uid: string) => number): FieldRecord[] {
  const list = liveRecords(state).filter((record) => !(record.origin?.hidden));
  const position = new Map(list.map((record, index) => [record, index]));
  return [...list].sort(
    (a, b) =>
      pageIndex(a.pageUid) - pageIndex(b.pageUid) ||
      a.def.tab_index - b.def.tab_index ||
      (position.get(a) ?? 0) - (position.get(b) ?? 0),
  );
}

/** The next tab index for a new field: after every other one. */
export const nextTabIndex = (state: FormState) =>
  Math.min(MAX_TAB_INDEX, liveRecords(state).reduce((max, record) => Math.max(max, record.def.tab_index), 0) + 10);

/** A definition for a new field of this type. */
export function newDef(type: FieldType, name: string, rect: Rect, tabIndex: number, exportValue?: string): FieldDef {
  const def: FieldDef = { name, type, rect: rectOf(rect), tab_index: tabIndex };
  if (type === 'dropdown') def.options = ['Option 1', 'Option 2'];
  if (type === 'radio') def.export_value = exportValue ?? 'Choice1';
  return cleanDef(def);
}

/** The key a record added by op `opId` has. */
export const addedKey = (opId: string) => `n${opId}`;
