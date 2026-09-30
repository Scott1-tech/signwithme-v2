import { useEffect, type RefObject } from 'react';

/**
 * While a press that STARTED ON A PAGE is held -- a drawing drag, an
 * annotation / slot drag or resize, a click -- the banner block above the
 * pages keeps the height (and bottom margin) it had when the press began.
 *
 * Why (fix 2026-09-27): every gesture maps the pointer through the page's
 * CURRENT box (`ToolCaptureLayer.pointOf` reads `getBoundingClientRect` on
 * every move and on release -- right for a page that scrolls under the
 * pointer). So anything that changes height above the pages mid-gesture slides
 * the page under a pointer that did not move, and the saved geometry is off by
 * exactly that much (live: a note cleared at drag start → 74 px → a whiteout
 * 19 pt tall saved as 60.5 pt). The passing note no longer lives in the
 * column at all (`EditorNote`); what is left there is the finalize error list,
 * the comparison error and the "same file as the template" warning -- all set
 * by async answers that can land mid-drag (a failed remove, a correction, the
 * comparison loading late). Frozen, such a change only shows once the press
 * is over; it is clipped (never lost) in the meantime.
 *
 * The press is seen in the CAPTURE phase on the stage, before any page
 * handler runs (they may clear or set banners as they start). The release is
 * seen on the window, and the unfreeze waits for the next task, so the
 * gesture's own `pointerup` handler still measures the page where it was.
 */
export function useGestureLayoutLock(
  stageRef: RefObject<HTMLElement | null>,
  blockRef: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    let locked: HTMLElement | null = null;
    let timer: number | undefined;

    const unlock = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
      if (!locked) return;
      locked.style.removeProperty('height');
      locked.style.removeProperty('margin-bottom');
      locked.style.removeProperty('overflow');
      delete locked.dataset.gestureLock;
      locked = null;
    };
    const onPress = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.closest('.pdf-page')) return;
      const block = blockRef.current;
      if (!block) return;
      unlock();
      const { height } = block.getBoundingClientRect();
      const { marginBottom } = window.getComputedStyle(block);
      block.style.height = `${height}px`;
      block.style.marginBottom = marginBottom;
      block.style.overflow = 'hidden';
      block.dataset.gestureLock = 'true';
      locked = block;
    };
    const onRelease = () => {
      if (!locked || timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        unlock();
      }, 0);
    };

    stage.addEventListener('pointerdown', onPress, true);
    window.addEventListener('pointerup', onRelease, true);
    window.addEventListener('pointercancel', onRelease, true);
    window.addEventListener('blur', unlock);
    return () => {
      stage.removeEventListener('pointerdown', onPress, true);
      window.removeEventListener('pointerup', onRelease, true);
      window.removeEventListener('pointercancel', onRelease, true);
      window.removeEventListener('blur', unlock);
      unlock();
    };
  }, [stageRef, blockRef]);
}
