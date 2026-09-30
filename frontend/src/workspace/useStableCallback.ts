import { useCallback, useLayoutEffect, useRef } from 'react';

/**
 * A callback whose identity never changes but which always runs the latest
 * version of `fn`. The workspace hands these to the memoised per-page overlay
 * (`overlay/PageOverlay.tsx`): a handler that is re-created on every render
 * would otherwise re-render every page on every keystroke.
 *
 * Only for event handlers -- never call the result during render.
 */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R) {
  const ref = useRef(fn);
  useLayoutEffect(() => {
    ref.current = fn;
  });
  return useCallback((...args: A) => ref.current(...args), []);
}
