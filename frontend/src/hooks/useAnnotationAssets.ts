import { useEffect, useMemo, useRef, useState } from 'react';
import { assetsApi } from '../api/assets';

/** A stored annotation picture, as a blob URL of the server's own PNG. */
export type AssetImageEntry = { url: string } | 'loading' | 'failed';

const revokeAll = (cache: Map<string, AssetImageEntry>) =>
  cache.forEach((entry) => {
    if (typeof entry === 'object') URL.revokeObjectURL(entry.url);
  });

/**
 * The pictures the `image` annotations point at (W4), fetched ONCE each
 * with the auth header (`GET .../annotation-assets/{asset_id}`) and held as
 * blob URLs: fetched when an id first appears, released when the last
 * annotation using it is gone (or the contract changes), a failure remembered
 * rather than retried in a loop. A fresh Map per change, because the per-page
 * overlay is memoised on identities.
 *
 * Unlike `useSignatureImages`, the cache is NOT dropped by an effect cleanup:
 * React's StrictMode unmounts and remounts every component once in
 * development, and a cleanup that cleared the cache would fetch every picture
 * twice. The cache is tied to the contract id instead, and released for good
 * only when the component really goes (a remount within the same tick cancels
 * the release).
 */
export function useAnnotationAssets(
  contractId: string | undefined,
  assetIds: readonly string[],
): ReadonlyMap<string, AssetImageEntry> {
  const cache = useRef(new Map<string, AssetImageEntry>());
  const owner = useRef<string | undefined>(undefined);
  const generation = useRef(0);
  const release = useRef<number | null>(null);
  const [version, setVersion] = useState(0);
  const idsKey = Array.from(new Set(assetIds)).sort().join('|');

  // Unmount: release the blob URLs -- unless this was StrictMode's rehearsal.
  useEffect(() => {
    if (release.current !== null) {
      window.clearTimeout(release.current);
      release.current = null;
    }
    return () => {
      release.current = window.setTimeout(() => {
        generation.current += 1;
        revokeAll(cache.current);
        cache.current = new Map();
        owner.current = undefined;
      }, 0);
    };
  }, []);

  useEffect(() => {
    if (!contractId) return;
    let changed = false;
    if (owner.current !== contractId) {
      // Another contract: another set of pictures.
      generation.current += 1;
      revokeAll(cache.current);
      cache.current = new Map();
      owner.current = contractId;
      changed = true;
    }
    const wanted = new Set(idsKey ? idsKey.split('|') : []);
    cache.current.forEach((entry, id) => {
      if (wanted.has(id)) return;
      if (typeof entry === 'object') URL.revokeObjectURL(entry.url);
      cache.current.delete(id);
      changed = true;
    });
    const run = generation.current;
    wanted.forEach((id) => {
      if (cache.current.has(id)) return;
      cache.current.set(id, 'loading');
      changed = true;
      void (async () => {
        let entry: AssetImageEntry = 'failed';
        try {
          entry = { url: URL.createObjectURL(await assetsApi.blob(contractId, id)) };
        } catch {
          /* stays 'failed' */
        }
        if (run !== generation.current || cache.current.get(id) !== 'loading') {
          if (typeof entry === 'object') URL.revokeObjectURL(entry.url);
          return;
        }
        cache.current.set(id, entry);
        setVersion((value) => value + 1);
      })();
    });
    if (changed) setVersion((value) => value + 1);
  }, [contractId, idsKey]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- `version` is the change signal
  return useMemo(() => new Map(cache.current), [version]);
}
