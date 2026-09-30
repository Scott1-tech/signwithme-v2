import { useCallback, useMemo, useState } from 'react';

/**
 * The workspace's ONE selection (plan §2.3).
 *
 * Before W1 there were three: the editor's `selectedId` (an annotation), the
 * signing page's `focusedSlotId` (a slot card / box) and the review page's
 * `activeFieldId` (the issue the sidebar points at). They lived on three
 * screens, so they could never disagree; on one screen they would. Here they
 * are one value, and each of the old names is a *view* of it:
 *
 *  * `annotationId` -- the annotation the toolbar edits and Delete removes;
 *  * `slotFieldId`  -- the slot marked `slot--active` and whose card is
 *    highlighted; an `issue` selection points at its field too, which is how a
 *    sidebar click lights up the slot on the page;
 *  * `signatureId`  -- a free signature picked from the Objects list.
 *
 * Only an annotation selection is something Delete / the arrow keys act on;
 * server rows are removed with their own button (see `SIGNATURE_LOCK_NOTE`).
 */
export type Selection =
  | { type: 'annotations'; ids: string[] }
  | { type: 'slot'; fieldId: string }
  | { type: 'signature'; id: string }
  | { type: 'issue'; key: string; fieldId: string | null }
  | null;

export function useSelection() {
  const [selection, setSelection] = useState<Selection>(null);

  /** Select one annotation; `null` drops an annotation selection (and only that). */
  const selectAnnotation = useCallback((id: string | null) => {
    if (id) {
      setSelection((current) =>
        current?.type === 'annotations' && current.ids.length === 1 && current.ids[0] === id
          ? current
          : { type: 'annotations', ids: [id] },
      );
    }
    else setSelection((current) => (current?.type === 'annotations' ? null : current));
  }, []);

  /**
   * Select several annotations as ONE selection -- a group (WE: the whiteout
   * and the text of an edited line). The first id is the primary one: the
   * toolbar describes it and the arrow keys move it.
   */
  const selectAnnotations = useCallback((ids: readonly string[]) => {
    if (ids.length > 0) {
      // The same ids again (a press on an already selected mark) keep the SAME
      // selection object: every memoised page reads its identity (W2 memo).
      setSelection((current) =>
        current?.type === 'annotations' &&
        current.ids.length === ids.length &&
        current.ids.every((id, index) => id === ids[index])
          ? current
          : { type: 'annotations', ids: [...ids] },
      );
    } else setSelection((current) => (current?.type === 'annotations' ? null : current));
  }, []);

  const selectSlot = useCallback((fieldId: string | null) => {
    setSelection(fieldId ? { type: 'slot', fieldId } : null);
  }, []);

  const selectIssue = useCallback((key: string, fieldId: string | null) => {
    setSelection({ type: 'issue', key, fieldId });
  }, []);

  const selectSignature = useCallback((id: string) => {
    setSelection({ type: 'signature', id });
  }, []);

  const clear = useCallback(() => setSelection(null), []);

  return useMemo(() => {
    const annotationId =
      selection?.type === 'annotations' ? (selection.ids[0] ?? null) : null;
    const slotFieldId =
      selection?.type === 'slot'
        ? selection.fieldId
        : selection?.type === 'issue'
          ? selection.fieldId
          : null;
    const signatureId = selection?.type === 'signature' ? selection.id : null;
    const annotationIds: readonly string[] =
      selection?.type === 'annotations' ? selection.ids : NO_IDS;
    return {
      selection,
      annotationId,
      annotationIds,
      slotFieldId,
      signatureId,
      selectAnnotation,
      selectAnnotations,
      selectSlot,
      selectIssue,
      selectSignature,
      clear,
    };
  }, [selection, selectAnnotation, selectAnnotations, selectSlot, selectIssue, selectSignature, clear]);
}

const NO_IDS: readonly string[] = [];

export type SelectionApi = ReturnType<typeof useSelection>;
