import { useCallback, useEffect, useRef, useState } from 'react';
import {
  DEFAULT_LAYOUT,
  loadLayout,
  sanitizeLayout,
  saveLayout,
  type FinalizeLayout,
} from '../lib/layout';
import type { DocumentHistory } from '../workspace/state/useDocumentHistory';

export type LayoutChange = FinalizeLayout | ((current: FinalizeLayout) => FinalizeLayout);

/** How a user change is named in the history, and whether it merges (a slider). */
export interface LayoutCommitMeta {
  label: string;
  coalesce?: string;
}

/**
 * The signer's position/appearance settings for one contract, kept in
 * `localStorage` under `swm.layout.<contractId>` so a reload restores them.
 * A preference only: finalize sends it and the server decides.
 *
 * TWO WAYS TO CHANGE IT (W2b, plan §2.3)
 * --------------------------------------
 *  * `commit(next, meta)` -- the USER changed it: a slot dragged or resized, a
 *    stepper, a slider, "Move together", Reset. One step of the workspace's
 *    history (channel `layout`), so Ctrl+Z puts the slot back.
 *  * `replace(next)` -- the APP corrected it: the page-edge clamp, the fit
 *    before finalize, dropping slot ids the server did not know. The present
 *    changes and is saved, but no step is added -- undo must never "un-clamp"
 *    a slot back off its page, and a correction is not something the user did.
 *
 * A change that leaves the (sanitized) layout as it was is nothing at all:
 * no write, no step.
 */
export function useContractLayout(
  contractId: string | undefined,
  history?: Pick<DocumentHistory, 'record' | 'register'>,
) {
  const [layout, setLayout] = useState<FinalizeLayout>(() => loadLayout(contractId));
  // The layout as of the last write in this tick (see `useAnnotations` for why
  // a step is never recorded inside a `setState` updater).
  const present = useRef(layout);
  const loadedFor = useRef(contractId);

  useEffect(() => {
    if (loadedFor.current === contractId) return;
    loadedFor.current = contractId;
    const next = loadLayout(contractId);
    present.current = next;
    setLayout(next);
  }, [contractId]);

  /** Sanitize, compare, write, save. Returns the before/after pair, or null for no change. */
  const write = useCallback(
    (next: LayoutChange): { before: FinalizeLayout; after: FinalizeLayout } | null => {
      const before = present.current;
      const after = sanitizeLayout(typeof next === 'function' ? next(before) : next);
      if (after === before || JSON.stringify(after) === JSON.stringify(before)) return null;
      present.current = after;
      setLayout(after);
      saveLayout(contractId, after);
      return { before, after };
    },
    [contractId],
  );

  const record = history?.record;
  const commit = useCallback(
    (next: LayoutChange, meta: LayoutCommitMeta = { label: 'Change layout' }) => {
      const change = write(next);
      if (!change || !record) return;
      record({ channel: 'layout', label: meta.label, coalesce: meta.coalesce, ...change });
    },
    [write, record],
  );

  const replace = useCallback((next: LayoutChange) => void write(next), [write]);

  // Undo / redo hand a whole layout back; it is written like any other value
  // (and the page-edge clamp then looks at it like any other value).
  const register = history?.register;
  useEffect(() => {
    if (!register) return undefined;
    return register('layout', (value) => void write(value as FinalizeLayout));
  }, [register, write]);

  const reset = useCallback(() => commit(DEFAULT_LAYOUT, { label: 'Reset layout' }), [commit]);

  return { layout, commit, replace, reset };
}
