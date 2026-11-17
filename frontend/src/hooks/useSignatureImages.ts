import { useEffect, useMemo, useRef, useState } from 'react';
import { signaturesApi } from '../api/signatures';
import { inkMetrics, readPixels, type InkMetrics } from '../lib/signatureInk';

export interface SignatureImage {
  /** Blob URL of the server-processed PNG (background removed, cropped to ink). */
  url: string;
  width: number;
  height: number;
  /**
   * Where the PDF builder finds this image's ink (`_ink_metrics`, mirrored in
   * `lib/signatureInk.ts`); `null` when the pixels could not be read. Only the
   * free-signature preview uses it (`FreeSignatureLayer`).
   */
  ink?: InkMetrics | null;
}

export type SignatureImageEntry = SignatureImage | 'loading' | 'failed';

function measure(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error('Signature image could not be decoded'));
    image.src = url;
  });
}

/** Size + ink box from the pixels; the size alone if they cannot be read. */
async function measureInk(url: string): Promise<{ width: number; height: number; ink: InkMetrics | null }> {
  try {
    const pixels = await readPixels(url);
    return { width: pixels.width, height: pixels.height, ink: inkMetrics(pixels) };
  } catch {
    return { ...(await measure(url)), ink: null };
  }
}

/**
 * The placed signatures' images, fetched once each with the auth header and
 * held as blob URLs. A signature is fetched when it first appears, released
 * when it goes away, and never re-fetched on an ordinary re-render. A failed
 * fetch (e.g. an older server without the image route) is remembered rather
 * than retried in a loop.
 */
export function useSignatureImages(
  contractId: string | undefined,
  signatureIds: readonly string[],
  /**
   * The stored image's hash per signature id. One signature placed in thirty
   * slots is thirty rows with ONE image: rows with the same hash share a
   * single download (and a single blob URL).
   */
  hashOf?: (signatureId: string) => string | undefined,
): ReadonlyMap<string, SignatureImageEntry> {
  const cache = useRef(new Map<string, SignatureImageEntry>());
  const hashOfRef = useRef(hashOf);
  hashOfRef.current = hashOf;
  /** Downloads in flight or done, by image hash; `ids` are the rows using each. */
  const shared = useRef(new Map<string, { image: Promise<SignatureImage | null>; ids: Set<string> }>());
  // Bumped whenever the cache is thrown away, so a fetch that was in flight
  // for the old contract (or a StrictMode double-mount) cannot write into it.
  const generation = useRef(0);
  const [version, setVersion] = useState(0);
  const idsKey = signatureIds.join('|');

  useEffect(
    () => () => {
      generation.current += 1;
      new Set(
        [...cache.current.values()].flatMap((entry) => (typeof entry === 'object' ? [entry.url] : [])),
      ).forEach((url) => URL.revokeObjectURL(url));
      cache.current = new Map();
      shared.current = new Map();
    },
    [contractId],
  );

  useEffect(() => {
    if (!contractId) return;
    const wanted = new Set(idsKey ? idsKey.split('|') : []);
    let changed = false;

    /** The row stops using its image; the blob goes with its last user. */
    const release = (id: string, entry: SignatureImageEntry | undefined) => {
      let stillUsed = false;
      shared.current.forEach((group, hash) => {
        if (!group.ids.delete(id)) return;
        if (group.ids.size === 0) shared.current.delete(hash);
        else stillUsed = true;
      });
      if (typeof entry === 'object' && !stillUsed) URL.revokeObjectURL(entry.url);
    };

    cache.current.forEach((entry, id) => {
      if (wanted.has(id)) return;
      cache.current.delete(id);
      release(id, entry);
      changed = true;
    });

    const run = generation.current;
    const download = async (id: string): Promise<SignatureImage | null> => {
      try {
        const blob = await signaturesApi.imageBlob(contractId, id);
        const url = URL.createObjectURL(blob);
        try {
          return { url, ...(await measureInk(url)) };
        } catch {
          URL.revokeObjectURL(url);
          return null;
        }
      } catch {
        return null;
      }
    };
    wanted.forEach((id) => {
      if (cache.current.has(id)) return;
      cache.current.set(id, 'loading');
      changed = true;
      const hash = hashOfRef.current?.(id) || `id:${id}`;
      let group = shared.current.get(hash);
      if (!group) {
        group = { image: download(id), ids: new Set() };
        shared.current.set(hash, group);
      }
      group.ids.add(id);
      const mine = group;
      void mine.image.then((image) => {
        if (run !== generation.current || cache.current.get(id) !== 'loading') {
          // Left while loading: the blob is dropped once nobody uses it.
          if (image && (run !== generation.current || mine.ids.size === 0)) URL.revokeObjectURL(image.url);
          return;
        }
        if (!image) {
          // A failed download is not shared: the next row asks again.
          if (shared.current.get(hash) === mine) shared.current.delete(hash);
          cache.current.set(id, 'failed');
        } else {
          cache.current.set(id, image);
        }
        setVersion((version) => version + 1);
      });
    });

    if (changed) setVersion((version) => version + 1);
  }, [contractId, idsKey]);

  // A fresh snapshot per change: the workspace's per-page overlay is memoised,
  // and a Map mutated in place would look unchanged to it (W1).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `version` is the change signal
  return useMemo(() => new Map(cache.current), [version]);
}
