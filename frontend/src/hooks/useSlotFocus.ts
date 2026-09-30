import { useCallback, useEffect, useRef, useState } from 'react';
import { HOVER_GRACE_MS, slotCardId } from '../lib/signing';
import type { SignatureSlot } from '../types/comparison';

/**
 * Which slot the signing page is pointing at, in three senses:
 * `focusedSlotId` -- the one last picked (its card and its box are marked);
 * `hoveredSlotId` -- the one under the pointer, which the on-page mini panel
 *   follows (hiding is delayed by `HOVER_GRACE_MS` so the pointer can travel
 *   from the slot to the panel);
 * `collapsedPages` -- the page groups folded away in the slot list, which a
 *   focus unfolds again.
 * Since W1 `focusedSlotId` is not state of its own: it is the workspace's
 * single selection (`workspace/state/useSelection.ts`), handed in, so a slot
 * picked here, an issue picked in the review sidebar and an annotation picked
 * on the page can never all be "selected" at once.
 */
export function useSlotFocus({
  slotById,
  scrollToPage,
  focusedSlotId,
  setFocusedSlotId,
}: {
  slotById: ReadonlyMap<string, SignatureSlot>;
  scrollToPage: (page: number) => void;
  focusedSlotId: string | null;
  setFocusedSlotId: (fieldId: string | null) => void;
}) {
  const [hoveredSlotId, setHoveredSlotId] = useState<string | null>(null);
  const hoverTimer = useRef<number | null>(null);
  // Pages whose group in the slot list is folded away.
  const [collapsedPages, setCollapsedPages] = useState<ReadonlySet<number>>(
    () => new Set<number>(),
  );

  // -- the on-page mini panel: show/hide with a grace period ----------------
  const showSlotTools = useCallback((fieldId: string) => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    setHoveredSlotId(fieldId);
  }, []);

  const hideSlotTools = useCallback(() => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => {
      hoverTimer.current = null;
      setHoveredSlotId(null);
    }, HOVER_GRACE_MS);
  }, []);

  useEffect(
    () => () => {
      if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    },
    [],
  );

  const togglePageGroup = useCallback((page: number) => {
    setCollapsedPages((current) => {
      const next = new Set(current);
      if (next.has(page)) next.delete(page);
      else next.add(page);
      return next;
    });
  }, []);

  /** A folded-away page cannot hide a slot the page is trying to point at. */
  const revealPage = useCallback((page: number | null | undefined) => {
    if (!page) return;
    setCollapsedPages((current) => {
      if (!current.has(page)) return current;
      const next = new Set(current);
      next.delete(page);
      return next;
    });
  }, []);

  // Clicking a slot on the page brings its card into view in the panel. Paired
  // dates live inside their signature's card, so every field_id has its own
  // anchor element regardless of where it is rendered. The lookup waits a frame
  // because the card's page group may only be unfolding now.
  const focusSlot = useCallback(
    (fieldId: string) => {
      setFocusedSlotId(fieldId);
      revealPage(slotById.get(fieldId)?.page);
      window.requestAnimationFrame(() => {
        const element = document.getElementById(slotCardId(fieldId));
        element?.scrollIntoView({ block: 'nearest' });
        element?.querySelector('input')?.focus();
      });
    },
    [revealPage, slotById, setFocusedSlotId],
  );

  /** The list's "Show" button: bring the document to the slot, not the other way. */
  const jumpToSlot = useCallback(
    (slot: SignatureSlot) => {
      if (slot.page) scrollToPage(slot.page);
      setFocusedSlotId(slot.field_id);
    },
    [scrollToPage, setFocusedSlotId],
  );

  return {
    focusedSlotId,
    hoveredSlotId,
    collapsedPages,
    showSlotTools,
    hideSlotTools,
    togglePageGroup,
    focusSlot,
    jumpToSlot,
  };
}

export type SlotFocus = ReturnType<typeof useSlotFocus>;
