import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { userSignaturesApi } from '../api/userSignatures';
import { serverTimeMs } from '../lib/time';
import type { UserSignature } from '../types/contract';

/**
 * The current user's reusable signature library.
 *
 * `loaded` turns true after the first answer, success or not, so a page can
 * tell "no saved signatures" from "not asked yet". `reload()` never rejects:
 * a failed refresh keeps the list it had.
 */
export function useSignatureLibrary() {
  const [items, setItems] = useState<UserSignature[]>([]);
  const [loaded, setLoaded] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reload = useCallback(
    (): Promise<void> =>
      userSignaturesApi
        .list()
        .then((rows) => {
          if (alive.current) setItems(rows);
        })
        .catch(() => undefined)
        .finally(() => {
          if (alive.current) setLoaded(true);
        }),
    [],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  // Which stored signature the automatic fill uses: the one the user marked as
  // default, otherwise the most recently created.
  const defaultSignature = useMemo<UserSignature | null>(() => {
    if (items.length === 0) return null;
    const marked = items.find((signature) => signature.is_default);
    if (marked) return marked;
    // Compared as instants: a mix of `...Z` and offset-less values (a server
    // restarted mid-session) would not sort right as plain strings.
    return [...items].sort((a, b) => serverTimeMs(b.created_at) - serverTimeMs(a.created_at))[0];
  }, [items]);

  return { items, loaded, reload, defaultSignature };
}
