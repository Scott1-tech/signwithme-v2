import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  groupIdsOf,
  isSized,
  isTextual,
  isWhiteout,
  loadAnnotations,
  moveAnnotation,
  recolorAnnotation,
  refillAnnotation,
  refontAnnotation,
  restrokeAnnotation,
  resizeAnnotation,
  restyleAnnotation,
  retypeAnnotation,
  saveAnnotations,
  withAnnotation,
  withAnnotations,
  withoutAnnotations,
  type Annotation,
  type AnnotationColor,
  type AnnotationFont,
} from '../lib/annotations';
import type { DocumentHistory } from '../workspace/state/useDocumentHistory';

/**
 * The editor's annotations. Their undo history is the workspace's ONE history
 * (`workspace/state/useDocumentHistory.ts`, W2), channel `annotations`.
 *
 * WHAT IS AND IS NOT A STEP
 * -------------------------
 * A step is a *committed* change, and every step is a whole new list (the
 * previous one is what Undo puts back -- see the history's header for why
 * snapshots). A drag in flight is not one: the gesture hook previews it and
 * calls `move` exactly once, on release, so pulling a mark across the page
 * costs one press of Ctrl+Z rather than forty. Anything that would leave the
 * list unchanged (dragging a mark back where it started, retyping the same
 * text) commits nothing at all -- `lib/annotations.ts` returns the same array,
 * and an identical array is not a step.
 *
 * GROUPS (WE, plan §7-6)
 * ----------------------
 * "Edit existing text" is a `whiteout` + `text` pair sharing a `group_id`. It
 * is added in ONE step (`addMany`), changed in one (`update`), and removed in
 * one: `remove` of either member takes the whole group. The whiteout of a
 * group is LOCKED -- `move` refuses it -- because it has to stay over the line
 * it hides; the text moves freely.
 *
 * WHY A REF NEXT TO THE STATE
 * ---------------------------
 * The step has to be recorded with its `before` and `after` in the handler
 * that made it. A `setState` updater may run later, and twice under
 * StrictMode, so it is no place to record anything; `present` is the list as
 * of the last commit *in this tick*, so two commits in one batch still chain.
 *
 * Persistence is a side effect of the present, per contract, in the style of
 * `useContractLayout`: `swm.annotations.<contractId>`, best-effort, and the
 * history itself is deliberately NOT stored -- a reload starts a fresh one.
 */

/** Steps kept. Kept for callers that quote it; the history enforces it. */
export { HISTORY_LIMIT as UNDO_LIMIT } from '../workspace/state/useDocumentHistory';

/** What one "edit" of a run may change at once (one undo step). */
export interface TextChange {
  text?: string;
  font?: AnnotationFont;
  size_pt?: number;
  color?: AnnotationColor;
  bold?: boolean;
  italic?: boolean;
}

export interface AnnotationStore {
  items: Annotation[];
  /** The annotation with this id, if it is still there. */
  get: (id: string | null) => Annotation | undefined;
  /**
   * Place a new annotation. It arrives with its id already decided
   * (`makeAnnotationId`): a `useState` updater may run later than the handler
   * that queued it, and twice over in StrictMode, so an id minted *inside* one
   * could neither be returned to the caller nor be the same on both runs.
   * A full list (500) is silently no change -- the caller checks first.
   */
  add: (annotation: Annotation) => void;
  /** Several at once, ONE step, all or none (a group). */
  addMany: (annotations: Annotation[], label: string) => void;
  /** New point, in PDF points (already clamped to the page): the baseline of a
   *  run, the centre of a mark. A grouped whiteout does not move. */
  move: (id: string, x_pt: number, y_pt: number) => void;
  /** New size, in points -- clamped to the *kind's* range (48 pt for a run,
   *  72 pt for a mark), which is why the id and not a kind is what comes in. */
  resize: (id: string, size_pt: number) => void;
  /** Both are no-ops on a mark, which carries neither. */
  setText: (id: string, text: string) => void;
  setFont: (id: string, font: AnnotationFont) => void;
  /** Bold / italic of a run (contract v6); `false` removes the key. */
  setStyle: (id: string, style: { bold?: boolean; italic?: boolean }) => void;
  /** v5 colour; black removes the key. */
  setColor: (id: string, color: AnnotationColor) => void;
  /** Several properties of one run, ONE step (the edit-text commit). */
  update: (id: string, change: TextChange, label: string) => void;
  /** Removes the annotation -- and, for a group member, the whole group. */
  remove: (id: string, label?: string) => void;
  /**
   * W4: the annotation with this id replaced by `next` (same id, same kind) --
   * a handle drag, a retyped note -- ONE step. Nothing when it is unchanged.
   */
  reshape: (id: string, next: Annotation, label: string) => void;
  /** W4: stroke width of an ink / shape / line (0.5…12 pt). */
  setStroke: (id: string, stroke_pt: number) => void;
  /** W4: a rect / ellipse filled (translucent) or not. */
  setFill: (id: string, fill: boolean) => void;
  /**
   * W5: the app's OWN correction of the whole list -- the clamp of stored
   * annotations onto their pages once the sizes are known. NOT a step (plan
   * §2.3, like the layout's `replace`): Undo goes back to what the user did,
   * and anything it brings back off a page is clamped again.
   */
  replace: (next: Annotation[]) => void;
}

export function useAnnotations(
  contractId: string | undefined,
  history: Pick<DocumentHistory, 'record' | 'register'>,
  /**
   * F1b: false once the list lives on the server (`useMarkupSync`): the
   * browser's `swm.annotations.<cid>` is then legacy data, kept for the
   * migration, and no longer written.
   */
  persistLocal = true,
): AnnotationStore {
  const [items, setItems] = useState<Annotation[]>(() => loadAnnotations(contractId));
  const present = useRef(items);
  const loadedFor = useRef(contractId);
  const persistRef = useRef(persistLocal);
  persistRef.current = persistLocal;

  // A different contract is a different document: its annotations are loaded
  // (the history resets itself on the same change).
  useEffect(() => {
    if (loadedFor.current === contractId) return;
    loadedFor.current = contractId;
    const next = loadAnnotations(contractId);
    present.current = next;
    setItems(next);
  }, [contractId]);

  // Only ever write a list back under the contract it was loaded for.
  useEffect(() => {
    if (loadedFor.current !== contractId || !persistRef.current) return;
    saveAnnotations(contractId, items);
  }, [contractId, items]);

  // Undo / redo hand a whole list back.
  const { record, register } = history;
  useEffect(
    () =>
      register('annotations', (value) => {
        const next = value as Annotation[];
        present.current = next;
        setItems(next);
      }),
    [register],
  );

  const commit = useCallback(
    (label: string, next: (current: Annotation[]) => Annotation[]) => {
      const before = present.current;
      const after = next(before);
      // Nothing changed: not a step, and the redo stack is left alone.
      if (after === before) return;
      present.current = after;
      setItems(after);
      record({ channel: 'annotations', label, before, after });
    },
    [record],
  );

  const patch = useCallback(
    (label: string, id: string, change: (annotation: Annotation) => Annotation) => {
      commit(label, (list) => {
        const current = list.find((item) => item.id === id);
        if (!current) return list;
        return withAnnotation(list, change(current));
      });
    },
    [commit],
  );

  const add = useCallback(
    (annotation: Annotation) => {
      // An empty run is not an annotation: the server refuses it and nothing
      // would be visible on the page to take it back with. A mark has no text
      // at all and is therefore never empty in this sense.
      if (isTextual(annotation) && annotation.text.trim() === '') return;
      commit(`Place ${annotation.kind}`, (list) =>
        withAnnotation(
          list,
          isSized(annotation) ? resizeAnnotation(annotation, annotation.size_pt) : annotation,
        ),
      );
    },
    [commit],
  );

  const addMany = useCallback(
    (annotations: Annotation[], label: string) => {
      if (annotations.some((item) => isTextual(item) && item.text.trim() === '')) return;
      const ready = annotations.map((item) =>
        isSized(item) ? resizeAnnotation(item, item.size_pt) : item,
      );
      commit(label, (list) => withAnnotations(list, ready));
    },
    [commit],
  );

  const move = useCallback(
    (id: string, x_pt: number, y_pt: number) =>
      patch('Move annotation', id, (annotation) =>
        // The whiteout of an edited line stays where the line is (plan §7-6).
        isWhiteout(annotation) && annotation.group_id
          ? annotation
          : moveAnnotation(annotation, x_pt, y_pt),
      ),
    [patch],
  );

  const resize = useCallback(
    (id: string, size_pt: number) =>
      patch('Resize annotation', id, (annotation) => resizeAnnotation(annotation, size_pt)),
    [patch],
  );

  const setText = useCallback(
    (id: string, text: string) => {
      const value = text.trim();
      // The server refuses an empty run; clearing the box is a cancel, not a
      // delete (the Delete key and the toolbar button are how a mark goes).
      if (!value) return;
      patch('Edit text', id, (annotation) => retypeAnnotation(annotation, text));
    },
    [patch],
  );

  const setFont = useCallback(
    (id: string, font: AnnotationFont) =>
      patch('Change font', id, (annotation) => refontAnnotation(annotation, font)),
    [patch],
  );

  const setStyle = useCallback(
    (id: string, style: { bold?: boolean; italic?: boolean }) =>
      patch(
        style.bold !== undefined ? 'Bold' : 'Italic',
        id,
        (annotation) => restyleAnnotation(annotation, style),
      ),
    [patch],
  );

  const setColor = useCallback(
    (id: string, color: AnnotationColor) =>
      patch('Change colour', id, (annotation) => recolorAnnotation(annotation, color)),
    [patch],
  );

  const update = useCallback(
    (id: string, change: TextChange, label: string) => {
      if (change.text !== undefined && change.text.trim() === '') return;
      patch(label, id, (annotation) => {
        let next = annotation;
        if (change.text !== undefined) next = retypeAnnotation(next, change.text);
        if (change.font !== undefined) next = refontAnnotation(next, change.font);
        if (change.size_pt !== undefined) next = resizeAnnotation(next, change.size_pt);
        if (change.color !== undefined) next = recolorAnnotation(next, change.color);
        if (change.bold !== undefined || change.italic !== undefined) {
          next = restyleAnnotation(next, { bold: change.bold, italic: change.italic });
        }
        return next;
      });
    },
    [patch],
  );

  const remove = useCallback(
    (id: string, label?: string) =>
      commit(label ?? 'Delete annotation', (list) => withoutAnnotations(list, groupIdsOf(list, id))),
    [commit],
  );

  const reshape = useCallback(
    (id: string, next: Annotation, label: string) =>
      patch(label, id, (annotation) =>
        next.id === annotation.id && next.kind === annotation.kind ? next : annotation,
      ),
    [patch],
  );

  const setStroke = useCallback(
    (id: string, stroke_pt: number) =>
      patch('Change stroke', id, (annotation) => restrokeAnnotation(annotation, stroke_pt)),
    [patch],
  );

  const setFill = useCallback(
    (id: string, fill: boolean) =>
      patch(fill ? 'Fill shape' : 'Unfill shape', id, (annotation) => refillAnnotation(annotation, fill)),
    [patch],
  );

  const replace = useCallback((next: Annotation[]) => {
    if (next === present.current) return;
    present.current = next;
    setItems(next);
  }, []);

  const get = useCallback(
    (id: string | null) => (id ? items.find((item) => item.id === id) : undefined),
    [items],
  );

  return useMemo(
    () => ({
      items,
      get,
      add,
      addMany,
      move,
      resize,
      setText,
      setFont,
      setStyle,
      setColor,
      update,
      remove,
      reshape,
      setStroke,
      setFill,
      replace,
    }),
    [
      replace,
      items,
      get,
      add,
      addMany,
      move,
      resize,
      setText,
      setFont,
      setStyle,
      setColor,
      update,
      remove,
      reshape,
      setStroke,
      setFill,
    ],
  );
}
