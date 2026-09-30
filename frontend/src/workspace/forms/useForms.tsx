import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import type { Rect } from '../../api/document';
import {
  formsApi,
  type CapabilitiesB3,
  type DocumentOpB3,
  type FieldOut,
  type FieldSuggestionOut,
  type FieldType,
} from '../../api/forms';
import type { EditorDocument } from '../../hooks/useContractDocument';
import type { PdfViewerState } from '../../hooks/usePdfViewer';
import { snapPt } from '../../lib/annotations';
import { pageExtent } from '../../lib/annotationKinds/shared';
import { mapRectPt, unrotateVector, UNROTATED } from '../../lib/viewport';
import type { EditSession } from '../session/useEditSession';
import { newOpId } from '../session/useEditSession';
import { dragBox } from '../tools/drawing';
import type { Gesture, Point, ToolId } from '../tools/types';
import { FieldDetectPanel, type Suggestion } from './FieldDetectPanel';
import { FieldLayer, type FieldView, type FormsMode, type SuggestionView } from './FieldLayer';
import {
  CLICK_SIZE,
  FIELD_TYPE_LABEL,
  MIN_FIELD_PT,
  addedKey,
  charsetMessage,
  cleanDef,
  fieldOpsOf,
  freshExportValue,
  freshName,
  generate,
  liveRecords,
  nameProblem,
  newDef,
  nextTabIndex,
  parse,
  radioGroup,
  rectOf,
  sanitizeName,
  tabOrder,
  takenNames,
  valueOf,
  winAnsiProblem,
  withFieldOps,
  NAME_STEM,
  type FieldRecord,
  type FieldValue,
  type FormState,
} from './fieldModel';
import { FIELD_TOOL_TYPE, formsBridge } from './fieldTools';
import { FieldProperties, type FieldPatch } from './FieldProperties';
import { TabOrderPanel } from './TabOrderPanel';

/**
 * F2b (EDITOR-V2-PLAN §7.4, §9 F2): interactive PDF forms in the workspace.
 *
 * THREE WAYS THE FIELDS ARE SHOWN (`mode`, from the armed tool):
 *  * `edit`   a field tool of Forms ▾ is armed: every field is a box with its
 *             name; a drag on empty paper draws a new one; a press on a field
 *             selects it (8 handles, move, resize, Delete, arrows), the
 *             «Form field» panel (`FieldProperties`) edits it, «Tab order»
 *             reorders the page's fields;
 *  * `fill`   Select is armed: every field is an HTML control over its box
 *             (typing, ticking, choosing) -- it never moves; the value is a
 *             `field.value` op. The contractor who opened the contract by
 *             e-mail (§11 Q2: `can_fill`, not `can_edit`) only ever has this;
 *  * `detect` «Detect form fields»: the fields are outlines, the server's
 *             suggestions for the pages on screen are dashed frames that can
 *             be moved / resized / retyped and are only added when accepted
 *             (`FieldDetectPanel`);
 *  * `passive` any other tool: outlines that take no pointer (the tool works
 *             through them).
 *
 * Everything the user does is ONE change of the edit session's op list
 * (`session.changeOps`: one `'ops'` undo step, one autosave), written by
 * `fieldModel.generate`. A change while the document is only being viewed
 * starts the session first -- a drawing tool needs `can_edit`, a filled value
 * `can_fill`. The fields themselves come from `GET …/versions/{no}/fields`
 * (the saved version's, the uploaded PDF's own AcroForm included); a
 * Docusign envelope's hidden signature field (`hidden: true`) is never drawn.
 */

export type { FormsMode };

interface Options {
  contractId: string | undefined;
  session: EditSession;
  editor: EditorDocument;
  viewer: PdfViewerState;
  toolId: ToolId;
  setTool: (id: ToolId) => void;
  /** The page uids as the viewer shows them now (index = position − 1). */
  shownUids: readonly string[];
  setNote: (note: string | null) => void;
  /** Let go of whatever else is selected (an annotation, a slot). */
  clearOtherSelection: () => void;
}

interface DetectState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  items: Suggestion[];
  error: string | null;
  pages: number[];
}

const DETECT_IDLE: DetectState = { status: 'idle', items: [], error: null, pages: [] };

const sameValue = (a: FieldValue | null, b: FieldValue | null) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

const snap = (pt: Point): Point => ({ x: snapPt(pt.x), y: snapPt(pt.y) });

type ChangeOps = (label: string, update: (list: DocumentOpB3[]) => DocumentOpB3[]) => void;

const REASON_TEXT: Record<string, string> = {
  not_permitted: 'Only an admin, a reviewer or the person who uploaded the contract can add or change form fields',
  contract_completed: 'The contract is finalised; its form fields cannot change',
  contract_busy: 'The contract is being analysed — try again when it has finished',
};

export function describeFieldProblem(reason: string): string {
  switch (reason) {
    case 'field_name_taken':
      return 'Another field already has this name.';
    case 'radio_value_taken':
      return 'Another button of this radio group already has this value.';
    case 'unknown_field':
      return 'This field is no longer in the document.';
    case 'invalid_field_value':
      return 'The value does not fit this field.';
    case 'field_readonly':
      return 'The field is read-only.';
    case 'field_not_fillable':
      return 'A signature field is signed, not filled.';
    case 'unsupported_field':
      return 'This kind of field cannot be changed here.';
    case 'undrawable_character':
      return 'A character cannot be drawn in a form field (Latin / Windows-1252 only).';
    case 'op_outside_page':
      return 'The field reaches outside the page.';
    case 'fill_only':
      return 'You may fill the fields of this document, not change them.';
    default:
      return reason;
  }
}

export function useForms({
  contractId,
  session,
  editor,
  viewer,
  toolId,
  setTool,
  shownUids,
  setNote,
  clearOtherSelection,
}: Options) {
  const doc = editor.doc;
  const versionNo = doc?.current_version.version_no ?? null;
  /**
   * ROTFIX: a field's rect is the ENGINE's (unrotated page, EDITOR-V2-PLAN
   * §0.2), so the page it is kept inside is the unrotated one too.
   */
  const flatSizeOf = useCallback(
    (page: number): { width: number; height: number } | null => {
      const frame = viewer.frameOf(page);
      return frame ? { width: frame.width, height: frame.height } : (viewer.pageSizes.get(page) ?? null);
    },
    [viewer],
  );
  const versionKey = contractId && versionNo !== null ? `${contractId}|${versionNo}|${doc?.current_version.file_hash}` : null;
  const [base, setBase] = useState<{ key: string; fields: FieldOut[] } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (editor.status !== 'ready' || !contractId || versionNo === null || !versionKey) return undefined;
    let live = true;
    setLoadError(null);
    formsApi
      .fields(contractId, versionNo)
      .then((fields) => {
        if (live) setBase({ key: versionKey, fields });
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setBase(null);
        setLoadError(cause instanceof Error ? cause.message : 'The form fields could not be read');
      });
    return () => {
      live = false;
    };
  }, [editor.status, contractId, versionNo, versionKey]);

  const baseFields = useMemo(() => (base && base.key === versionKey ? base.fields : null), [base, versionKey]);
  const available = editor.status === 'ready' && baseFields !== null;
  const baseRef = useRef<readonly FieldOut[]>([]);
  baseRef.current = baseFields ?? [];

  const sessionRef = useRef(session);
  sessionRef.current = session;
  const caps = session.capabilities as CapabilitiesB3 | null;
  const canEdit = Boolean(caps?.can_edit);
  const canFill = Boolean(caps?.can_fill ?? caps?.can_edit);
  const editReason = canEdit ? null : (REASON_TEXT[caps?.reason ?? ''] ?? 'The document cannot be edited right now');
  const fillReason = canFill
    ? null
    : caps?.reason === 'contract_completed'
      ? 'The contract is finalised; its form fields cannot be filled any more'
      : 'The form cannot be filled right now';

  const fieldOps = useMemo(() => fieldOpsOf(session.ops as DocumentOpB3[]), [session.ops]);
  const state: FormState = useMemo(() => parse(baseFields ?? [], fieldOps), [baseFields, fieldOps]);
  const stateRef = useRef(state);
  stateRef.current = state;

  const drawType = FIELD_TOOL_TYPE[toolId] ?? null;
  const mode: FormsMode = !available
    ? 'passive'
    : drawType
      ? 'edit'
      : toolId === 'field-detect'
        ? 'detect'
        : toolId === 'select'
          ? 'fill'
          : 'passive';

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showTabs, setShowTabs] = useState(false);
  useEffect(() => {
    if (mode !== 'edit') {
      setSelectedKey(null);
      setShowTabs(false);
    }
  }, [mode]);
  const selected = selectedKey ? (liveRecords(state).find((record) => record.key === selectedKey) ?? null) : null;
  useEffect(() => {
    // An undo took the selected field away.
    if (selectedKey && !selected) setSelectedKey(null);
  }, [selectedKey, selected]);

  const select = useCallback(
    (key: string | null) => {
      if (key) clearOtherSelection();
      setSelectedKey(key);
      if (key) setShowTabs(false);
    },
    [clearOtherSelection],
  );

  /* -- the session --------------------------------------------------------------- */

  // Several quick changes (type, tick, choose) before the draft exists share ONE start.
  const startingRef = useRef<Promise<boolean> | null>(null);
  const ensureSession = useCallback(
    async (purpose: 'edit' | 'fill'): Promise<boolean> => {
      const current = sessionRef.current;
      if (current.mode === 'edit') return true;
      if (startingRef.current) return startingRef.current;
      if (!current.available) {
        setNote('The document editor is not available for this contract.');
        return false;
      }
      const capsNow = current.capabilities as CapabilitiesB3 | null;
      const allowed = purpose === 'edit' ? Boolean(capsNow?.can_edit) : Boolean(capsNow?.can_fill ?? capsNow?.can_edit);
      if (!allowed) {
        setNote(purpose === 'edit' ? (editReason ?? 'Form fields cannot be changed now.') : (fillReason ?? 'The form cannot be filled now.'));
        return false;
      }
      const starting = current.start().then(Boolean);
      startingRef.current = starting;
      try {
        return await starting;
      } finally {
        startingRef.current = null;
      }
    },
    [editReason, fillReason, setNote],
  );

  /** ONE user change of the fields: parse the op list, change, write the field ops back. */
  const change = useCallback(
    async (label: string, purpose: 'edit' | 'fill', mutate: (draft: FormState) => boolean | void): Promise<boolean> => {
      if (!(await ensureSession(purpose))) return false;
      let applied = true;
      const changeOps = sessionRef.current.changeOps as unknown as ChangeOps;
      changeOps(label, (list) => {
        const draft = parse(baseRef.current, fieldOpsOf(list));
        if (mutate(draft) === false) {
          applied = false;
          return list;
        }
        return withFieldOps(list, generate(draft));
      });
      return applied;
    },
    [ensureSession],
  );

  const findIn = (draft: FormState, key: string) => liveRecords(draft).find((record) => record.key === key) ?? null;

  /* -- adding ------------------------------------------------------------------------ */

  const uidOfPage = useCallback((page: number) => shownUids[page - 1] ?? null, [shownUids]);
  const pageIndex = useCallback((uid: string) => {
    const at = shownUids.indexOf(uid);
    return at < 0 ? 100000 : at;
  }, [shownUids]);

  const addField = useCallback(
    async (type: FieldType, pageUid: string, box: Rect) => {
      const opId = newOpId();
      const joinGroup =
        type === 'radio' && selected && selected.def.type === 'radio' && selected.pageUid === pageUid ? selected.def.name : null;
      const done = await change(`Add ${FIELD_TYPE_LABEL[type].toLowerCase()}`, 'edit', (draft) => {
        const taken = takenNames(draft);
        let name = freshName(NAME_STEM[type], taken);
        let exportValue: string | undefined;
        if (type === 'radio') {
          if (joinGroup && radioGroup(draft, joinGroup).length > 0) {
            name = joinGroup;
            exportValue = freshExportValue(new Set(radioGroup(draft, joinGroup).map((item) => item.def.export_value ?? '')));
          } else {
            exportValue = 'Choice1';
          }
        }
        draft.records.push({
          key: addedKey(opId),
          origin: null,
          def: newDef(type, name, box, nextTabIndex(draft), exportValue),
          baseDef: null,
          pageUid,
          removed: false,
          opId,
          lockedReason: null,
          baseValue: null,
        });
      });
      if (done) {
        select(addedKey(opId));
        setShowTabs(false);
      }
    },
    [change, select, selected],
  );

  const beginDraw = useCallback(
    (type: FieldType, ctx: { page: number; pageSize: { width: number; height: number } | null; notify: (note: string | null) => void; selection: { selectAnnotation: (id: string | null) => void } }, pt: Point): Gesture | null => {
      if (!sessionRef.current.available || !available) {
        ctx.notify('Form fields need the server’s document editor, which is not available for this contract.');
        return null;
      }
      const capsNow = sessionRef.current.capabilities as CapabilitiesB3 | null;
      if (!capsNow?.can_edit) {
        ctx.notify(editReason ?? 'Form fields cannot be added now.');
        return null;
      }
      const uid = uidOfPage(ctx.page);
      if (!uid) return null;
      ctx.selection.selectAnnotation(null);
      ctx.notify(null);
      const start = snap(pt);
      let end = start;
      const extent = pageExtent(ctx.pageSize);
      const box = () => dragBox(start, end, false, ctx.pageSize);
      return {
        move(next) {
          end = snap(next);
        },
        end(next) {
          end = snap(next);
          let drawn = box();
          if (drawn.w < 2 && drawn.h < 2) {
            // A click: a field of the usual size, centred there, inside the page.
            const size = CLICK_SIZE[type];
            drawn = {
              x: Math.min(Math.max(0, start.x - size.w / 2), Math.max(0, extent.width - size.w)),
              y: Math.min(Math.max(0, start.y - size.h / 2), Math.max(0, extent.height - size.h)),
              w: Math.min(size.w, extent.width),
              h: Math.min(size.h, extent.height),
            };
          }
          drawn = {
            ...drawn,
            w: Math.max(MIN_FIELD_PT, Math.min(drawn.w, extent.width - drawn.x)),
            h: Math.max(MIN_FIELD_PT, Math.min(drawn.h, extent.height - drawn.y)),
          };
          // ROTFIX: drawn on the page as displayed; a field lives on the unrotated page.
          const frame = viewer.frameOf(ctx.page);
          if (frame && frame.rotation !== 0) drawn = mapRectPt(drawn, frame, frame.rotation, UNROTATED);
          void addField(type, uid, { x_pt: drawn.x, y_pt: drawn.y, w_pt: drawn.w, h_pt: drawn.h });
        },
        cancel() {
          /* nothing was added */
        },
        Preview: ({ scale }) => {
          const drawn = box();
          return (
            <rect
              className="fm-capture-preview"
              data-testid="capture-preview"
              data-kind={`field-${type}`}
              data-box-pt={`${drawn.x},${drawn.y},${drawn.w},${drawn.h}`}
              x={drawn.x * scale}
              y={drawn.y * scale}
              width={drawn.w * scale}
              height={drawn.h * scale}
            />
          );
        },
      };
    },
    [addField, available, editReason, uidOfPage, viewer],
  );

  useEffect(() => {
    const bridge = { beginDraw };
    formsBridge.current = bridge;
    return () => {
      if (formsBridge.current === bridge) formsBridge.current = null;
    };
  }, [beginDraw]);

  /* -- changing ------------------------------------------------------------------------ */

  const setRect = useCallback(
    (key: string, rect: Rect, label = 'Move form field') =>
      change(label, 'edit', (draft) => {
        const record = findIn(draft, key);
        if (!record || record.lockedReason) return false;
        record.def = cleanDef({ ...record.def, rect: rectOf(rect) });
      }),
    [change],
  );

  /** A property change from the panel. A rename of a radio button renames its whole group (unless it joins another). */
  const patch = useCallback(
    (key: string, next: FieldPatch, label: string) =>
      change(label, 'edit', (draft) => {
        const record = findIn(draft, key);
        if (!record || record.lockedReason) return false;
        const oldName = record.def.name;
        const rename = next.name !== undefined && next.name !== oldName ? next.name : null;
        if (rename !== null && nameProblem(draft, record, rename)) return false;
        const { name: _ignored, ...rest } = next;
        void _ignored;
        if (record.def.type === 'radio') {
          const group = radioGroup(draft, oldName);
          const joining = rename !== null && radioGroup(draft, rename).length > 0;
          // Group-level properties follow the group (PDF: one field, several buttons).
          const groupKeys: Array<keyof FieldPatch> = ['required', 'readonly', 'tooltip'];
          const shared = Object.fromEntries(Object.entries(rest).filter(([k]) => groupKeys.includes(k as keyof FieldPatch)));
          if (rename !== null && joining) {
            const values = new Set(radioGroup(draft, rename).map((item) => item.def.export_value ?? ''));
            const ev = rest.export_value ?? record.def.export_value ?? 'Choice1';
            record.def = cleanDef({ ...record.def, ...rest, name: rename, export_value: values.has(ev) ? freshExportValue(values) : ev });
          } else {
            group.forEach((item) => {
              if (item.lockedReason) return;
              item.def = cleanDef({
                ...item.def,
                ...(item === record ? rest : shared),
                ...(rename !== null ? { name: rename } : {}),
                // One default per group: this button's tick clears the others'.
                ...(item !== record && rest.default === true ? { default: false } : {}),
              });
            });
            if (rename !== null) moveValue(draft, oldName, rename);
          }
          return;
        }
        record.def = cleanDef({ ...record.def, ...rest, ...(rename !== null ? { name: rename } : {}) });
        if (rename !== null) moveValue(draft, oldName, rename);
      }),
    [change],
  );

  const remove = useCallback(
    (key: string) =>
      change('Delete form field', 'edit', (draft) => {
        const record = findIn(draft, key);
        if (!record) return false;
        const targets =
          record.origin && record.def.type !== 'radio'
            ? liveRecords(draft).filter((item) => item.origin && item.origin.name === record.origin?.name)
            : [record];
        targets.forEach((item) => {
          if (item.origin) {
            item.removed = true;
            item.opId = null;
          } else {
            draft.records.splice(draft.records.indexOf(item), 1);
          }
        });
      }).then((done) => {
        if (done) setSelectedKey(null);
        return done;
      }),
    [change],
  );

  const duplicate = useCallback(
    async (key: string) => {
      const opId = newOpId();
      const done = await change('Duplicate form field', 'edit', (draft) => {
        const record = findIn(draft, key);
        if (!record) return false;
        const { x_pt, y_pt, w_pt, h_pt } = record.def.rect;
        const page = shownUids.indexOf(record.pageUid) + 1;
        // ROTFIX: a field rect is the engine's (unrotated): so is the page it stays inside.
        const height = (page > 0 ? flatSizeOf(page)?.height : undefined) ?? 792;
        const below = y_pt + h_pt + 6;
        const y = below + h_pt <= height ? below : Math.max(0, y_pt - h_pt - 6);
        let name: string;
        let exportValue: string | undefined;
        if (record.def.type === 'radio') {
          name = record.def.name;
          exportValue = freshExportValue(new Set(radioGroup(draft, name).map((item) => item.def.export_value ?? '')));
        } else {
          name = freshName(record.def.name.replace(/_\d+$/, '') || NAME_STEM[record.def.type], takenNames(draft));
        }
        draft.records.push({
          key: addedKey(opId),
          origin: null,
          def: cleanDef({
            ...record.def,
            name,
            rect: { x_pt, y_pt: y, w_pt, h_pt },
            default: record.def.type === 'radio' ? false : record.def.default,
            ...(exportValue ? { export_value: exportValue } : {}),
            tab_index: nextTabIndex(draft),
          }),
          baseDef: null,
          pageUid: record.pageUid,
          removed: false,
          opId,
          lockedReason: null,
          baseValue: null,
        });
      });
      if (done) select(addedKey(opId));
    },
    [change, select, shownUids, flatSizeOf],
  );

  /** Fill a field. Refused up front (nothing sent) when a character cannot be drawn. */
  const setValue = useCallback(
    async (key: string, value: FieldValue): Promise<boolean> => {
      const record = liveRecords(stateRef.current).find((item) => item.key === key);
      if (!record) return false;
      if (typeof value === 'string') {
        const bad = winAnsiProblem(value, record.def.type === 'multiline');
        if (bad) {
          setNote(charsetMessage(bad));
          return false;
        }
      }
      return change(`Fill «${record.def.name}»`, 'fill', (draft) => {
        const target = findIn(draft, key);
        if (!target) return false;
        const name = target.def.name;
        const saved = draft.values.get(name);
        draft.values.delete(name);
        const plain = valueOf(draft, target);
        if (!sameValue(plain, value)) draft.values.set(name, { value, opId: saved?.opId ?? newOpId() });
        else if (!saved) return false;
      });
    },
    [change, setNote],
  );

  const reorder = useCallback(
    (keys: readonly string[]) =>
      change('Change tab order', 'edit', (draft) => {
        let changed = false;
        keys.forEach((key, index) => {
          const record = findIn(draft, key);
          if (!record || record.lockedReason) return;
          const tab = (index + 1) * 10;
          if (record.def.tab_index === tab) return;
          record.def = cleanDef({ ...record.def, tab_index: tab });
          changed = true;
        });
        if (!changed) return false;
      }),
    [change],
  );

  /* -- keyboard (edit mode) -------------------------------------------------------- */

  useEffect(() => {
    if (mode !== 'edit' || !selectedKey) return undefined;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('input, textarea, select, [contenteditable="true"], [role="toolbar"], [role="menu"], .modal')) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const record = liveRecords(stateRef.current).find((item) => item.key === selectedKey);
      if (!record) return;
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        void remove(record.key);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        setSelectedKey(null);
      } else if (event.key.startsWith('Arrow') && !record.lockedReason) {
        event.preventDefault();
        const step = event.shiftKey ? 10 : 1;
        const page = shownUids.indexOf(record.pageUid) + 1;
        // ROTFIX: an arrow moves the field that way ON SCREEN; the rect is unrotated.
        const { x: dx, y: dy } = unrotateVector(
          event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0,
          event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0,
          viewer.frameOf(page)?.rotation ?? 0,
        );
        const size = flatSizeOf(page);
        const { x_pt, y_pt, w_pt, h_pt } = record.def.rect;
        const x = Math.min(Math.max(0, x_pt + dx), (size?.width ?? 1e5) - w_pt);
        const y = Math.min(Math.max(0, y_pt + dy), (size?.height ?? 1e5) - h_pt);
        void setRect(record.key, { x_pt: x, y_pt: y, w_pt, h_pt }, 'Nudge form field');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, remove, selectedKey, setRect, shownUids, flatSizeOf, viewer]);

  /* -- detect ------------------------------------------------------------------------ */

  const [detect, setDetect] = useState<DetectState>(DETECT_IDLE);
  const [pickedSuggestion, setPickedSuggestion] = useState<string | null>(null);
  const detectToken = useRef(0);

  const visiblePages = useCallback((): number[] => {
    const stage = viewer.containerRef.current;
    if (!stage) return [viewer.currentPage];
    const frame = stage.getBoundingClientRect();
    const pages = Array.from(stage.querySelectorAll<HTMLElement>('.pdf-page[data-page-number]'))
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return box.bottom > frame.top + 1 && box.top < frame.bottom - 1 && box.height > 0;
      })
      .map((element) => Number(element.dataset.pageNumber))
      .filter((page) => Number.isFinite(page) && page > 0);
    return pages.length > 0 ? pages : [viewer.currentPage];
  }, [viewer]);

  const runDetect = useCallback(async () => {
    if (!contractId || !available) return;
    const versionUids = new Set(editor.pages.map((page) => page.page_uid));
    const pages = visiblePages().filter((page) => {
      const uid = shownUids[page - 1];
      return uid && versionUids.has(uid);
    });
    if (pages.length === 0) {
      setDetect({ status: 'error', items: [], error: 'None of the pages on screen is a saved page of the document (save the page changes first).', pages: [] });
      return;
    }
    detectToken.current += 1;
    const token = detectToken.current;
    setDetect({ status: 'loading', items: [], error: null, pages });
    try {
      const out = await formsApi.detect(contractId, { page_uids: pages.map((page) => shownUids[page - 1]) });
      if (token !== detectToken.current) return;
      const live = liveRecords(stateRef.current);
      const overlaps = (s: FieldSuggestionOut) =>
        live.some((record) => {
          if (record.pageUid !== s.page_uid) return false;
          const a = record.def.rect;
          const b = s.rect;
          const w = Math.min(a.x_pt + a.w_pt, b.x_pt + b.w_pt) - Math.max(a.x_pt, b.x_pt);
          const h = Math.min(a.y_pt + a.h_pt, b.y_pt + b.h_pt) - Math.max(a.y_pt, b.y_pt);
          return w > 0 && h > 0 && w * h > 0.5 * Math.min(a.w_pt * a.h_pt, b.w_pt * b.h_pt);
        });
      const items = out.suggestions.filter((s) => !overlaps(s)).map((s) => ({ ...s, rect: rectOf(s.rect) }));
      setDetect({ status: 'ready', items, error: null, pages });
    } catch (cause) {
      if (token !== detectToken.current) return;
      setDetect({
        status: 'error',
        items: [],
        error: cause instanceof ApiError || cause instanceof Error ? cause.message : 'Detection failed',
        pages,
      });
    }
  }, [available, contractId, editor.pages, shownUids, visiblePages]);

  // Arming «Detect form fields» runs it on the pages on screen; leaving it drops the suggestions.
  useEffect(() => {
    if (mode === 'detect') {
      if (!canEdit) {
        setDetect({ status: 'error', items: [], error: editReason, pages: [] });
        return;
      }
      void runDetect();
      return;
    }
    detectToken.current += 1;
    setDetect(DETECT_IDLE);
    setPickedSuggestion(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on entering / leaving the mode
  }, [mode]);

  const acceptSuggestions = useCallback(
    async (ids: readonly string[]) => {
      const chosen = detect.items.filter((item) => ids.includes(item.suggestion_id));
      if (chosen.length === 0) return;
      const done = await change(
        chosen.length === 1 ? 'Accept suggested field' : `Accept ${chosen.length} suggested fields`,
        'edit',
        (draft) => {
          chosen.forEach((item) => {
            const taken = takenNames(draft);
            const wanted = sanitizeName(item.name || item.label, NAME_STEM[item.type]);
            const name = taken.has(wanted) ? freshName(wanted.replace(/_\d+$/, ''), taken) : wanted;
            const opId = newOpId();
            draft.records.push({
              key: addedKey(opId),
              origin: null,
              def: newDef(item.type, name, item.rect, nextTabIndex(draft), item.type === 'radio' ? 'Choice1' : undefined),
              baseDef: null,
              pageUid: item.page_uid,
              removed: false,
              opId,
              lockedReason: null,
              baseValue: null,
            });
          });
        },
      );
      if (done) {
        setDetect((current) => ({ ...current, items: current.items.filter((item) => !ids.includes(item.suggestion_id)) }));
        setNote(
          chosen.length === 1
            ? 'The suggested field was added to the draft. Save changes to keep it.'
            : `${chosen.length} suggested fields were added to the draft (one undo step). Save changes to keep them.`,
        );
      }
    },
    [change, detect.items, setNote],
  );

  const rejectSuggestion = useCallback((id: string) => {
    setDetect((current) => ({ ...current, items: current.items.filter((item) => item.suggestion_id !== id) }));
  }, []);

  const reshapeSuggestion = useCallback((id: string, next: Partial<Pick<Suggestion, 'rect' | 'type'>>) => {
    setDetect((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.suggestion_id === id ? { ...item, ...(next.rect ? { rect: rectOf(next.rect) } : {}), ...(next.type ? { type: next.type } : {}) } : item,
      ),
    }));
  }, []);

  /* -- what the page draws --------------------------------------------------------- */

  const problems = session.problems;
  const byPage = useMemo(() => {
    const map = new Map<string, FieldView[]>();
    liveRecords(state).forEach((record) => {
      if (record.origin?.hidden) return;
      const list = map.get(record.pageUid) ?? [];
      const problemReason =
        (record.opId && problems[record.opId]) ||
        (state.values.get(record.def.name) && problems[state.values.get(record.def.name)?.opId ?? '']) ||
        null;
      list.push({
        record,
        value: valueOf(state, record),
        problem: problemReason ? describeFieldProblem(problemReason) : null,
      });
      map.set(record.pageUid, list);
    });
    return map;
  }, [problems, state]);

  const suggestionsByPage = useMemo(() => {
    const map = new Map<string, SuggestionView[]>();
    detect.items.forEach((item) => {
      const list = map.get(item.page_uid) ?? [];
      list.push({ suggestion: item, picked: item.suggestion_id === pickedSuggestion });
      map.set(item.page_uid, list);
    });
    return map;
  }, [detect.items, pickedSuggestion]);

  const renderLayer = (pageNumber: number, uid: string): ReactNode => {
    if (!available) return null;
    const fields = byPage.get(uid) ?? [];
    const suggestions = mode === 'detect' ? (suggestionsByPage.get(uid) ?? []) : [];
    if (fields.length === 0 && suggestions.length === 0) return null;
    return (
      <FieldLayer
        key={`fm-${pageNumber}`}
        pageNumber={pageNumber}
        scale={viewer.scale}
        frame={viewer.frameOf(pageNumber)}
        pageSize={flatSizeOf(pageNumber)}
        mode={mode}
        fields={fields}
        suggestions={suggestions}
        selectedKey={selectedKey}
        canFill={canFill}
        fillReason={fillReason}
        onSelect={select}
        onRect={(key, rect, resized) => void setRect(key, rect, resized ? 'Resize form field' : 'Move form field')}
        onValue={setValue}
        onSuggestionPick={setPickedSuggestion}
        onSuggestionRect={(id, rect) => reshapeSuggestion(id, { rect })}
        onSuggestionAccept={(id) => void acceptSuggestions([id])}
        onSuggestionReject={rejectSuggestion}
      />
    );
  };

  const orderList = useMemo(() => tabOrder(state, pageIndex), [pageIndex, state]);

  let panel: ReactNode = null;
  if (available && mode === 'edit') {
    panel = (
      <div className="fm-dock" data-testid="forms-dock" role="region" aria-label="Form fields">
        <div className="fm-dock__head">
          <span className="fm-dock__title">
            Form fields <span className="fm-dock__count" data-testid="forms-count">{orderList.length}</span>
          </span>
          <button
            type="button"
            className={`sj-btn fm-dock__tabs${showTabs ? ' is-active' : ''}`}
            data-testid="field-tab-order-open"
            aria-pressed={showTabs}
            title="The order the Tab key visits the fields of each page"
            onClick={() => setShowTabs((value) => !value)}
          >
            Tab order
          </button>
        </div>
        {showTabs ? (
          <TabOrderPanel
            records={orderList}
            pageLabel={(uid) => {
              const at = shownUids.indexOf(uid);
              return at < 0 ? '?' : String(at + 1);
            }}
            onReorder={(keys) => void reorder(keys)}
            onClose={() => setShowTabs(false)}
          />
        ) : selected ? (
          <FieldProperties
            key={selected.key}
            record={selected}
            state={state}
            onPatch={(next, label) => patch(selected.key, next, label)}
            onDuplicate={() => void duplicate(selected.key)}
            onDelete={() => void remove(selected.key)}
            onClose={() => setSelectedKey(null)}
          />
        ) : (
          <p className="fm-dock__hint" data-testid="forms-hint">
            Drag on the page to draw a {FIELD_TYPE_LABEL[drawType ?? 'text'].toLowerCase()} (a click makes one of the usual
            size), or press a field to change it.
            {!canEdit && editReason ? <span className="fm-dock__reason"> {editReason}.</span> : null}
          </p>
        )}
      </div>
    );
  } else if (available && mode === 'detect') {
    panel = (
      <FieldDetectPanel
        status={detect.status}
        error={detect.error}
        pages={detect.pages}
        items={detect.items}
        picked={pickedSuggestion}
        onPick={setPickedSuggestion}
        onType={(id, type) => reshapeSuggestion(id, { type })}
        onAccept={(id) => void acceptSuggestions([id])}
        onReject={rejectSuggestion}
        onAcceptAll={() => void acceptSuggestions(detect.items.map((item) => item.suggestion_id))}
        onRun={() => void runDetect()}
        onClose={() => setTool('select')}
      />
    );
  }

  return {
    available,
    loadError,
    mode,
    state,
    selectedKey,
    canEdit,
    canFill,
    editReason,
    renderLayer,
    panel,
  };
}

export type Forms = ReturnType<typeof useForms>;

/** Move the value the user set from one field name to another (a rename). */
function moveValue(draft: FormState, from: string, to: string): void {
  const entry = draft.values.get(from);
  if (!entry) return;
  draft.values.delete(from);
  draft.values.set(to, entry);
}

export type { FieldRecord };
