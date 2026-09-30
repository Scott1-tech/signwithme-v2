import { useCallback, useState } from 'react';

/**
 * UI preferences that survive a reload.
 *
 * These are *preferences*, never authority: the server still decides what may
 * be signed and what a finalised document contains. Storage is best-effort --
 * in private mode `localStorage` throws, and the preference simply falls back
 * to its default for that session rather than breaking the page.
 */
function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the choice simply does not persist */
  }
}

/** A one-of-N choice. Unknown stored values are discarded, not trusted. */
export function useStoredChoice<T extends string>(key: string, allowed: readonly T[], fallback: T) {
  const [value, setValue] = useState<T>(() => {
    const stored = read(key) as T | null;
    return stored !== null && allowed.includes(stored) ? stored : fallback;
  });

  const update = useCallback(
    (next: T) => {
      setValue(next);
      write(key, next);
    },
    [key],
  );

  return [value, update] as const;
}

/** An on/off preference. Absent means "never chosen", so `fallback` wins. */
export function useStoredFlag(key: string, fallback: boolean) {
  const [value, setValue] = useState<boolean>(() => {
    const stored = read(key);
    if (stored === null) return fallback;
    return stored === 'true';
  });

  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      write(key, String(next));
    },
    [key],
  );

  return [value, update] as const;
}
