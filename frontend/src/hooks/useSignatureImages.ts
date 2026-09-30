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
): ReadonlyMap<string, SignatureImageEntry> {
  const cache = useRef(new Map<string, SignatureImageEntry>());
  // Bumped whenever the cache is thrown away, so a fetch that was in flight
  // for the old contract (or a StrictMode double-mount) cannot write into it.
  const generation = useRef(0);
  const [version, setVersion] = useState(0);
  const idsKey = signatureIds.join('|');

  useEffect(
    () => () => {
      generation.current += 1;
      cache.current.forEach((entry) => {
        if (typeof entry === 'object') URL.revokeObjectURL(entry.url);
      });
      cache.current = new Map();
    },
    [contractId],
  );

  useEffect(() => {
    if (!contractId) return;
    const wanted = new Set(idsKey ? idsKey.split('|') : []);
    let changed = false;

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
        let entry: SignatureImageEntry = 'failed';
        try {
          const blob = await signaturesApi.imageBlob(contractId, id);
          const url = URL.createObjectURL(blob);
          try {
            entry = { url, ...(await measureInk(url)) };
          } catch {
            URL.revokeObjectURL(url);
          }
        } catch {
          /* stays 'failed' */
        }
        if (run !== generation.current || cache.current.get(id) !== 'loading') {
          if (typeof entry === 'object') URL.revokeObjectURL(entry.url);
          return;
        }
        cache.current.set(id, entry);
        setVersion((version) => version + 1);
      })();
    });

    if (changed) setVersion((version) => version + 1);
  }, [contractId, idsKey]);

  // A fresh snapshot per change: the workspace's per-page overlay is memoised,
  // and a Map mutated in place would look unchanged to it (W1).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `version` is the change signal
  return useMemo(() => new Map(cache.current), [version]);
}
