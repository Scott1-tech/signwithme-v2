import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { LayoutSlotRow } from '../components/LayoutPanel';
import { useContractLayout, type LayoutChange } from './useContractLayout';
import { useSlotDrag } from './useSlotDrag';
import { useSlotResize } from './useSlotResize';
import type { PageSize } from './usePdfViewer';
import type { SignatureSlots } from './useSignatureSlots';
import { fitLayoutToPagesReport, type FinalizeLayout } from '../lib/layout';
import { buildLayoutRows } from '../lib/signing';
import type { DocumentHistory } from '../workspace/state/useDocumentHistory';

interface Options {
  contractId: string | undefined;
  slotSet: Pick<SignatureSlots, 'slots' | 'sigSlots' | 'dateSlots' | 'pairedDateFor'>;
  /** From the viewer: every page's size in PDF points, and the render scale. */
  pageSizes: ReadonlyMap<number, PageSize>;
  /**
   * ROTFIX: the pages the slots are measured on (the current version's, as
   * displayed) when they differ from the viewer's -- a draft that turns or
   * moves pages is on screen. The page-edge clamp, the drags and the panel
   * read these; `pageSizeOf` (returned) stays the viewer's.
   */
  slotPageSizes?: ReadonlyMap<number, PageSize>;
  scale: number;
  /** The workspace's one history: user gestures are steps of it (W2b). */
  history?: Pick<DocumentHistory, 'record' | 'register'>;
}

/**
 * Where the signing page's marks sit: the per-contract layout
 * (`swm.layout.<id>`), the two pointer gestures that edit it (move and the
 * corner resize), the page-edge clamp with its note, and the rows the
 * "Position & appearance" panel lists.
 *
 * `commit` is a user change (one undo step: drag end, resize end, a stepper,
 * a slider, "Move together", Reset); `replace` is the app's own correction
 * (the page-edge clamp here and before finalize) and is never a step.
 */
export function useSlotLayout({ contractId, slotSet, pageSizes, slotPageSizes, scale, history }: Options) {
  const { slots, sigSlots, dateSlots, pairedDateFor } = slotSet;

  // Position/appearance tuning for this contract (persisted per contract).
  const { layout, commit, replace, reset } = useContractLayout(contractId, history);
  const pageSizeOf = useCallback(
    (page: number | null) => (page ? (pageSizes.get(page) ?? null) : null),
    [pageSizes],
  );
  const slotSizes = slotPageSizes ?? pageSizes;
  const slotPageSizeOf = useCallback(
    (page: number | null) => (page ? (slotSizes.get(page) ?? null) : null),
    [slotSizes],
  );

  // The two gestures write through `commit`, each under its own name. Both
  // write once, on release (a double-click reset of a slot is its own step).
  const commitMove = useCallback(
    (next: (current: FinalizeLayout) => FinalizeLayout) => commit(next, { label: 'Move slot' }),
    [commit],
  );
  const commitResize = useCallback(
    (next: (current: FinalizeLayout) => FinalizeLayout) =>
      commit(next, { label: 'Resize signature' }),
    [commit],
  );

  // Dragging a slot box, a placed slot signature or a date text moves that
  // slot (x and y) for this contract only.
  const drag = useSlotDrag({ layout, update: commitMove, scale, pageSizeOf: slotPageSizeOf });
  // The corner handle: one signature's own size, kept apart from the move
  // gesture above (see the header of `hooks/useSlotResize.ts`).
  const resize = useSlotResize({ layout, update: commitResize, pageSizeOf: slotPageSizeOf });

  // The server does not keep marks on the page (it only enforces ±200 pt), so
  // whenever a global offset, the signature size or the date size pushes a
  // slot past a page edge, its own dx/dy is pulled back in here -- as a
  // `replace`: the correction is not a step, so Ctrl+Z goes back to what the
  // user did before, never to a slot hanging off the page.
  // The user is told when that happens; the note clears itself after a few
  // seconds or on the next change to the layout.
  const [clampNote, setClampNote] = useState<string | null>(null);
  const clampNoteLayout = useRef<FinalizeLayout | null>(null);
  useEffect(() => {
    if (slotSizes.size === 0 || slots.length === 0) return;
    const { layout: fitted, moved } = fitLayoutToPagesReport(layout, slots, slotPageSizeOf);
    if (moved.length > 0) {
      const count = moved.length;
      setClampNote(`${count} ${count === 1 ? 'slot was' : 'slots were'} moved to stay on the page.`);
      clampNoteLayout.current = null;
      replace(fitted);
      return;
    }
    // First settled layout after the note: remember it; any later change clears the note.
    if (clampNoteLayout.current === null) clampNoteLayout.current = layout;
    else if (clampNoteLayout.current !== layout) setClampNote(null);
  }, [layout, slots, slotSizes, slotPageSizeOf, replace]);
  useEffect(() => {
    if (!clampNote) return;
    const timer = window.setTimeout(() => setClampNote(null), 5000);
    return () => window.clearTimeout(timer);
  }, [clampNote]);

  // One row per positioned slot for the per-slot nudges.
  const scaleOf = resize.scaleOf;
  const rows = useMemo<LayoutSlotRow[]>(
    () => buildLayoutRows({ sigSlots, dateSlots, pairedDateFor, pageSizeOf: slotPageSizeOf, layout, scaleOf }),
    [sigSlots, dateSlots, pairedDateFor, slotPageSizeOf, layout, scaleOf],
  );

  return {
    layout,
    commit: commit as (next: LayoutChange, meta?: { label: string; coalesce?: string }) => void,
    replace,
    reset,
    pageSizeOf,
    drag,
    resize,
    clampNote,
    rows,
  };
}
