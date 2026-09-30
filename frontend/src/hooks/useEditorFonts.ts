import { useEffect, useState } from 'react';
import { fontsApi, type FontOut } from '../api/document';

/**
 * F2: `GET /api/fonts` (B2) -- the families the server can set new text in,
 * with their licence and what they stand in for. Asked ONCE per category per
 * page load (the list is static on the server); a failure leaves `null`, and
 * the pickers then offer only the families they know (never a dead entry).
 */

type Category = 'text' | 'signature';
const cache = new Map<Category, Promise<FontOut[] | null>>();

export function loadEditorFonts(category: Category = 'text'): Promise<FontOut[] | null> {
  let job = cache.get(category);
  if (!job) {
    job = fontsApi
      .list(category)
      .then((list) => (Array.isArray(list) ? list : null))
      .catch(() => {
        cache.delete(category);
        return null;
      });
    cache.set(category, job);
  }
  return job;
}

export function useEditorFonts(enabled: boolean, category: Category = 'text'): FontOut[] | null {
  const [fonts, setFonts] = useState<FontOut[] | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    void loadEditorFonts(category).then((list) => {
      if (live) setFonts(list);
    });
    return () => {
      live = false;
    };
  }, [enabled, category]);
  return fonts;
}
