/**
 * F2b: the interactive-form API (backend B3, EDITOR-V2-PLAN §3 `field.*`, §6).
 *
 * The contract itself lives in `document.ts` (the «B3» block, the mirror of
 * `backend/app/schemas/document.py`); this module is the one place the forms
 * UI (`workspace/forms/*`) imports it from, so the forms code never reaches
 * into the rest of the document API by accident.
 */
export {
  EXPORT_VALUE_RE,
  FIELD_NAME_RE,
  FIELD_OP_LAYERS,
  FIELD_SIZE_MAX_PT,
  FIELD_SIZE_MIN_PT,
  MAX_DETECT_PAGES,
  MAX_FIELD_OPTIONS,
  MAX_FIELD_VALUE_CHARS,
  MAX_OPTION_CHARS,
  MAX_TAB_INDEX,
  MAX_TOOLTIP_CHARS,
  formsApi,
  isFieldOp,
  isFormError,
} from './document';
export type {
  CapabilitiesB3,
  DetectFieldsBody,
  DetectFieldsOut,
  DocumentOpB3,
  DraftPutBodyB3,
  FieldAddOp,
  FieldDef,
  FieldOp,
  FieldOpKind,
  FieldOut,
  FieldRemoveOp,
  FieldSuggestionOut,
  FieldType,
  FieldUpdateOp,
  FieldValueOp,
  FormErrorCode,
} from './document';
