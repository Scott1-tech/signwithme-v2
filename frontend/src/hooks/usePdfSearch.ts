import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PageText, PdfViewerState } from './usePdfViewer';
import { mapRectPt, UNROTATED, type RectPt } from '../lib/viewport';
import { ensureFontLoaded } from '../lib/fonts';
import { advanceWithin } from '../lib/textRuns';

/**
 * Find text in the document the viewer already has open.
 *
 * There is no text *layer* in this viewer -- the pages are canvases, so the
 * browser's own Ctrl+F has nothing to find and a reader cannot select a word.
 * This hook is the replacement: it pulls each page's text out of pdf.js once,
 * keeps it, and turns a query into rectangles the page overlay can draw.
 *
 * How it stays cheap on a 66-page contract:
 *
 * * **The text is cached, the search is not.** `viewer.getPageText` keeps one
 *   parsed copy per page under a hard budget (`hooks/usePdfViewer.ts`), so the
 *   first query pays the extraction and every later query is a string scan --
 *   which is why the first one reports progress and the rest feel instant.
 * * **One page at a time, in page order.** Extraction goes through the same
 *   pdf.js worker that paints the document; ten pages asked for at once would
 *   stall the pages the reader is actually looking at.
 * * **Results are published as they are found**, so the first match can be
 *   jumped to while the tail of the document is still being read.
 * * **Bounded.** At most `MAX_HITS` matches are kept: a one-letter query on a
 *   long contract is otherwise tens of thousands of DOM nodes for something
 *   nobody can use.
 *
 * Matching is plain, case-insensitive substring -- no regular expressions, so
 * a query can contain any character the reader can type without it meaning
 * something else. Overlapping matches are not reported twice.
 */

/** Long enough for the reader to stop typing, short enough to feel live. */
const DEBOUNCE_MS = 180;
/** Matches kept for one query. Beyond this the count is shown as "N+". */
const MAX_HITS = 500;
/** Pages read between yields, so a long document does not freeze the tab. */
const PAGES_PER_SLICE = 4;

export interface SearchHit {
  page: number;
  /** Character range inside that page's text. */
  start: number;
  end: number;
  /** One rectangle per text run the match crosses, in PDF points, unrotated. */
  rects: RectPt[];
}

export interface PageHit {
  hit: SearchHit;
  /** Index into `hits`, so a mark knows whether it is the current one. */
  index: number;
}

export interface PdfSearchState {
  query: string;
  setQuery: (value: string) => void;
  /** Every match found so far, in document order. */
  hits: SearchHit[];
  /** Index of the match the reader is on, or -1 when there is none. */
  activeIndex: number;
  /** True while the document is still being read for the first time. */
  searching: boolean;
  /** Pages read / pages to read, for the first-search progress. */
  progress: { done: number; total: number };
  /** True when `MAX_HITS` cut the list short. */
  truncated: boolean;
  next: () => void;
  previous: () => void;
  /** Empty the query and drop every mark. */
  clear: () => void;
  /** The matches that belong on one page, with their global indices. */
  hitsOnPage: (pageNumber: number) => PageHit[];
}

/**
 * The rectangles for one match.
 *
 * A run of text is positioned as a whole, so a match that starts in the middle
 * of one has to be placed inside it. Since W3 those boxes can become printed
 * highlights («Highlight all matches»), so the split is measured, not counted:
 * the run's text in the Liberation face of its family (the same advance widths
 * as Helvetica / Times / Courier), scaled to the run's PDF width
 * (`lib/textRuns.ts advanceWithin`). A run without a known family falls back
 * to character count, as before.
 */
function rectsFor(pageText: PageText, start: number, end: number): RectPt[] {
  const rects: RectPt[] = [];
  for (const piece of pageText.pieces) {
    const pieceEnd = piece.start + piece.length;
    if (pieceEnd <= start) continue;
    if (piece.start >= end) break; // pieces are sorted, so nothing later can match
    const from = Math.max(start, piece.start);
    const to = Math.min(end, pieceEnd);
    if (to <= from || piece.length <= 0) continue;
    let left: number;
    let right: number;
    if (piece.family) {
      const run = pageText.text.slice(piece.start, pieceEnd);
      const at = (count: number) =>
        advanceWithin(run, piece.width, piece.family!, false, false, count);
      left = at(from - piece.start);
      right = at(to - piece.start);
    } else {
      left = (piece.width * (from - piece.start)) / piece.length;
      right = (piece.width * (to - piece.start)) / piece.length;
    }
    const flat = { x: piece.x + left, y: piece.y, w: Math.max(right - left, 1), h: piece.height };
    // ROTFIX: measured on the unrotated page; drawn on the page as displayed.
    const shown = pageText.frame ? mapRectPt(flat, pageText.frame, UNROTATED, pageText.frame.rotation) : flat;
    rects.push({ x: shown.x, y: shown.y, width: shown.w, height: shown.h });
  }
  return rects;
}

/** The faces the split is measured in; asked for before the first scan. */
const loadSearchFaces = () =>
  Promise.all(
    (['LiberationSans', 'LiberationSerif', 'LiberationMono'] as const).map((family) =>
      ensureFontLoaded(family),
    ),
  );

/** Every non-overlapping occurrence of `needle` on one page. */
function hitsOn(pageNumber: number, pageText: PageText, needle: string, budget: number): SearchHit[] {
  const found: SearchHit[] = [];
  if (needle.length === 0 || budget <= 0) return found;
  const hay = pageText.text.toLowerCase();
  let from = 0;
  for (;;) {
    const at = hay.indexOf(needle, from);
    if (at < 0) break;
    const end = at + needle.length;
    found.push({ page: pageNumber, start: at, end, rects: rectsFor(pageText, at, end) });
    if (found.length >= budget) break;
    from = end;
  }
  return found;
}

export function usePdfSearch(viewer: PdfViewerState): PdfSearchState {
  const { pageCount, getPageText, revealPoint } = viewer;

  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [done, setDone] = useState(0);
  const [truncated, setTruncated] = useState(false);

  /** The match the document was last moved to, so a result arriving late does
   *  not yank the reader back to where they already are. */
  const revealedRef = useRef('');

  // A new document (or an emptied query) invalidates everything.
  useEffect(() => {
    setHits([]);
    setActiveIndex(-1);
    setSearching(false);
    setDone(0);
    setTruncated(false);
    revealedRef.current = '';
  }, [pageCount]);

  useEffect(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length === 0 || pageCount === 0) {
      setHits([]);
      setActiveIndex(-1);
      setSearching(false);
      setDone(0);
      setTruncated(false);
      revealedRef.current = '';
      return;
    }

    // Drop the previous query's marks and counter *now*, not when the debounce
    // fires: for those 180 ms the reader would otherwise be looking at the
    // last query's answer under the new query's text.
    setHits([]);
    setActiveIndex(-1);
    setTruncated(false);
    setDone(0);
    setSearching(true);
    revealedRef.current = '';

    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        // The in-run split is measured in these faces (`rectsFor`).
        await loadSearchFaces();
        if (cancelled) return;
        const found: SearchHit[] = [];
        let cut = false;
        for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
          if (cancelled) return;
          const pageText = await getPageText(pageNumber);
          if (cancelled) return;
          if (pageText) {
            const page = hitsOn(pageNumber, pageText, needle, MAX_HITS - found.length);
            if (page.length > 0) {
              found.push(...page);
              // A fresh array each time: the marks are rendered from it.
              setHits([...found]);
              setActiveIndex((current) => (current < 0 ? 0 : current));
            }
          }
          setDone(pageNumber);
          if (found.length >= MAX_HITS) {
            cut = true;
            break;
          }
          // Hand the tab back regularly: extraction is worker-side, but the
          // scan and the re-renders are not.
          if (pageNumber % PAGES_PER_SLICE === 0) {
            await new Promise((resolve) => window.setTimeout(resolve, 0));
          }
        }
        if (cancelled) return;
        setTruncated(cut);
        setSearching(false);
      })();
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, pageCount, getPageText]);

  // Move the document to the match the reader is on -- once per match, so an
  // incoming page of results does not re-scroll them.
  useEffect(() => {
    const hit = activeIndex >= 0 ? hits[activeIndex] : undefined;
    if (!hit) return;
    const key = `${hit.page}:${hit.start}:${hit.end}`;
    if (revealedRef.current === key) return;
    revealedRef.current = key;
    const first = hit.rects[0];
    revealPoint(hit.page, first ? { x: first.x, y: first.y } : { x: 0, y: 0 });
  }, [activeIndex, hits, revealPoint]);

  const step = useCallback(
    (delta: number) => {
      setActiveIndex((current) => {
        if (hits.length === 0) return -1;
        if (current < 0) return delta > 0 ? 0 : hits.length - 1;
        return (current + delta + hits.length) % hits.length;
      });
    },
    [hits.length],
  );

  const next = useCallback(() => step(1), [step]);
  const previous = useCallback(() => step(-1), [step]);

  const clear = useCallback(() => {
    setQuery('');
  }, []);

  // page -> its matches, built once per result set instead of filtered by
  // every one of the pages on screen.
  const byPage = useMemo(() => {
    const map = new Map<number, PageHit[]>();
    hits.forEach((hit, index) => {
      const list = map.get(hit.page);
      if (list) list.push({ hit, index });
      else map.set(hit.page, [{ hit, index }]);
    });
    return map;
  }, [hits]);

  const hitsOnPage = useCallback((pageNumber: number) => byPage.get(pageNumber) ?? [], [byPage]);

  const progress = useMemo(() => ({ done, total: pageCount }), [done, pageCount]);

  return useMemo<PdfSearchState>(
    () => ({
      query,
      setQuery,
      hits,
      activeIndex,
      searching,
      progress,
      truncated,
      next,
      previous,
      clear,
      hitsOnPage,
    }),
    [query, hits, activeIndex, searching, progress, truncated, next, previous, clear, hitsOnPage],
  );
}
