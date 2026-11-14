import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist';
import {
  buildRuns,
  faceFromName,
  familyOf,
  runGeometry,
  type FontFaceInfo,
  type RunFontFamily,
  type TextItemLike,
  type TextRun,
  type TextStyleLike,
} from '../lib/textRuns';
import {
  mapRectPt,
  normalizeQuarter,
  rotatePointPx,
  rotatedSize,
  swapsAxes,
  UNROTATED,
  type PageFrame,
  type Point,
  type Quarter,
} from '../lib/viewport';

// Bundled worker: no CDN fetch, so the viewer works offline and behind a proxy.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

/**
 * Continuous scroll, virtualised.
 *
 * Every page of the document is in the DOM from the start, each one holding
 * **exactly** the space it will occupy (`pageSizes` x `scale`), so the scrollbar
 * is honest and nothing jumps when a page finally paints. Only the pages near
 * the viewport actually get pixels: a 66-page contract drawn in full would be
 * ~300-400 MB of canvas and takes the tab with it.
 *
 * The three rules that keep it bounded:
 *
 * * an `IntersectionObserver` says which pages touch the viewport; the render
 *   window is that band plus `RENDER_MARGIN_PAGES` either side, capped at
 *   `MAX_RENDERED_PAGES`;
 * * a page that leaves the window has its canvas reset to 0 x 0 -- that, not
 *   `clearRect`, is what hands the backing store back;
 * * at most `MAX_ACTIVE_RENDERS` pages paint at once, nearest page first, and a
 *   render whose scale or window membership went stale is cancelled rather than
 *   finished.
 *
 * `currentPage` is therefore a *result* of the scroll position (the page with
 * the most of itself on screen), not an input -- `scrollToPage` is how a caller
 * moves the document.
 */

/** Pages kept painted on each side of the band that is actually visible. */
const RENDER_MARGIN_PAGES = 2;
/** Hard ceiling on painted pages, for the zoomed-far-out case. */
const MAX_RENDERED_PAGES = 6;
/** Canvases painting at the same time. */
const MAX_ACTIVE_RENDERS = 2;
/** A 3x display would otherwise make every canvas nine times the memory. */
const MAX_DEVICE_PIXEL_RATIO = 2;

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const SCALE_STEP = 0.25;

/** Room left above a page when it is jumped to. */
const SCROLL_MARGIN_PX = 8;
/** Room left left/right of the page when fitting it to the container. */
const FIT_PADDING_PX = 32;
/** Where down the viewport `revealPoint` parks the thing it was asked for. */
const REVEAL_FRACTION = 0.32;

/* --- the text cache -------------------------------------------------------
 * Page text is cheap next to a bitmap -- a page of a contract is a few
 * thousand characters, not a few hundred thousand pixels -- but it is not
 * free, and a search must be allowed to walk a 66-page document without the
 * cache growing without a bound. The two budgets below are what the cache is
 * trimmed to, oldest page first (a `Map` keeps insertion order).
 *
 * Measured, not guessed. A 66-page document of 360 text runs and ~6 000
 * characters a page -- denser than the contracts this app is pointed at --
 * costs **2.55 MB** of retained heap once every page has been searched
 * (Chromium `performance.memory`, after `gc()`, marks cleared). That is
 * 23 760 pieces and ~428 000 characters, i.e. about a sixth of the piece
 * budget below, which therefore tops out around 16 MB for a document nobody
 * would open in a browser anyway.
 */
/** Characters of page text kept. ~3 MB of UTF-16 at the ceiling. */
const MAX_TEXT_CHARS = 1_500_000;
/** Positioned text pieces kept. ~16 MB of small objects at the ceiling. */
const MAX_TEXT_PIECES = 150_000;
/**
 * Raw `TextContent` kept for the text layer (plan §3), oldest page first. A
 * separate cache from the search's: the layer needs the items and their
 * styles as pdf.js hands them out, the search only its own joined string.
 * The layer is only ever on the ≤ `MAX_RENDERED_PAGES` pages of the window.
 */
const MAX_RAW_TEXT_PAGES = 10;
/** How long the layer waits for pdf.js to have a font (bold/italic) before guessing "regular". */
const FONT_WAIT_MS = 2000;

/** One page's text layer, once it is drawn (plan §3). */
export interface TextLayerHandle {
  /** One per pdf.js item, in content order, unrotated PDF points. */
  runs: TextRun[];
  /** Which run a `<span>` of the layer is, or -1. */
  spanIndexOf(element: Element): number;
}

/** What `renderTextLayer` hands back straight away. */
export interface TextLayerTask {
  /** Stop drawing and empty the container. Safe to call twice. */
  cancel(): void;
  /** The runs, or `null` when cancelled / the page would not parse. */
  done: Promise<TextLayerHandle | null>;
}

interface RawText {
  items: ReadonlyArray<TextItemLike | { type?: string }>;
  styles: Record<string, TextStyleLike>;
  // Kept as pdf.js returned it, to hand straight to `TextLayer`.
  content: unknown;
}

export interface PageSize {
  width: number;
  height: number;
}

/**
 * One run of text out of `getTextContent`, with the box it occupies in PDF
 * points, top-left origin, **unrotated** -- the same space `lib/layout.ts`
 * speaks and therefore the space a search mark can be handed to the overlay
 * in without a second conversion.
 */
export interface TextPiece {
  /** Offset of this piece's first character inside `PageText.text`. */
  start: number;
  length: number;
  x: number;
  y: number;
  width: number;
  height: number;
  /**
   * The run's family shape (pdf.js' own fallback family for its font), so a
   * match inside the run is split by real advance widths, not by character
   * count (W3: those boxes become printed highlights).
   */
  family?: RunFontFamily;
}

export interface PageText {
  /** Every run joined, a newline wherever pdf.js reports an end of line. */
  text: string;
  /** Sorted by `start`, non-overlapping; a newline belongs to no piece. */
  pieces: TextPiece[];
  /**
   * ROTFIX: on a page with its own `/Rotate` the pieces are measured on the
   * UNROTATED page (where a line of text is horizontal, so the box of a
   * word is exact) and this frame turns a box onto the page as displayed
   * (`mapRectPt(box, frame, UNROTATED, frame.rotation)`). `null` upright.
   */
  frame: PageFrame | null;
}

/** What `page.render()` hands back; only these two members are used here. */
interface CancellableRender {
  cancel: () => void;
  promise: Promise<void>;
}

export interface PdfViewerState {
  /** The scrolling element. Every page lives inside it. */
  containerRef: React.RefObject<HTMLDivElement>;
  /** Ref callback for a page's `.pdf-page` element. Stable identity. */
  registerPage: (pageNumber: number, element: HTMLDivElement | null) => void;
  /** Ref callback for a page's `<canvas>`. Stable identity. */
  registerCanvas: (pageNumber: number, canvas: HTMLCanvasElement | null) => void;
  pageCount: number;
  /** Derived: the page with the most of itself inside the viewport. Read-only
   *  on purpose -- the way to change it is to move the document. */
  currentPage: number;
  scrollToPage: (page: number) => void;
  /**
   * Bring one spot on one page into view: the page is scrolled to and then
   * the point (PDF points, unrotated, top-left origin) is parked about a
   * third of the way down the viewport. Used by the search to land on a
   * match rather than merely on the page it sits in.
   */
  revealPoint: (page: number, point: Point) => void;
  /** CSS pixels per PDF point. Overlays MUST multiply by this or they drift
   *  the moment the user zooms. */
  scale: number;
  setScale: (scale: number) => void;
  /** One page's rendered size in CSS pixels, **as it is laid out** -- width
   *  and height are swapped while that page is turned a quarter. This is the
   *  box `.pdf-page` must reserve; `pageSizes` stays unrotated. */
  sizeOf: (pageNumber: number) => PageSize;
  /**
   * Draw one page into a canvas the caller owns, `cssWidth` CSS pixels wide,
   * out of the document that is **already open** -- the thumbnail strip must
   * not fetch and parse the file a second time.
   *
   * Independent of the viewport bookkeeping above: it neither joins the render
   * window nor counts against `MAX_ACTIVE_RENDERS`, so the caller is the one
   * that has to keep the number of live thumbnail canvases bounded.
   *
   * Resolves `true` only when the canvas really holds that page; a failure
   * (no document yet, a cancelled render, a page that will not parse) resolves
   * `false` and is never surfaced as a viewer error -- a missing thumbnail must
   * not put an error banner over a document that reads perfectly well.
   */
  renderPageTo: (
    pageNumber: number,
    canvas: HTMLCanvasElement,
    cssWidth: number,
  ) => Promise<boolean>;
  /**
   * One page's text, out of the document that is **already open**, cached.
   *
   * Resolves `null` rather than throwing when there is no document, the page
   * is out of range, or pdf.js will not parse it: a search that finds nothing
   * on one page is not an error the reader should see over the document.
   *
   * The first call for a page costs a `getTextContent` round-trip through the
   * worker; every later call for it is free until the document changes.
   */
  getPageText: (pageNumber: number) => Promise<PageText | null>;
  /**
   * The pages that may hold pixels right now (the render window, ≤
   * `MAX_RENDERED_PAGES`). Replaced only when its membership changes, so its
   * identity is a signal. The text layer is mounted on these pages only.
   */
  windowPages: ReadonlySet<number>;
  /**
   * Draw one page's pdf.js text layer (4.8.69 `TextLayer`) into `container`,
   * which the caller positions over the page and gives `--scale-factor` (CSS
   * pixels per point). The spans are laid out in percentages and
   * `var(--scale-factor)`, so a zoom only has to update that variable.
   * `done` resolves with the page's runs (`lib/textRuns.ts`), the same
   * geometry the search uses.
   */
  renderTextLayer: (pageNumber: number, container: HTMLDivElement) => TextLayerTask;
  /** The current page's own `/Rotate`, in degrees. No consumer yet. */
  rotation: number;
  /**
   * ROTFIX: one page as the file on screen has it -- its own `/Rotate` (the
   * space `pageSizes` and the overlay box are in) and its UNROTATED size in
   * pt (the space of the PDF engine). `null` until the page is measured. The
   * reader's view turn is NOT in it (that one turns the whole overlay).
   */
  frameOf: (pageNumber: number) => PageFrame | null;
  /** The quarter turn the reader has applied to `pageNumber`, view only. */
  rotationOf: (pageNumber: number) => Quarter;
  /** `rotationOf(currentPage)`, so the toolbar can render without a callback. */
  viewRotation: Quarter;
  /** True while **any** page is turned -- what the "rotated view" notice reads. */
  anyRotated: boolean;
  /** Turn one page by `delta` degrees (rounded to a quarter). View only: the
   *  file, the stored layout and every coordinate stay exactly as they were. */
  rotatePage: (pageNumber: number, delta?: number) => void;
  /** Every page's size in PDF points (scale 1, its own `/Rotate` applied, the
   *  reader's quarter turn NOT applied), keyed by page number. */
  pageSizes: ReadonlyMap<number, PageSize>;
  loading: boolean;
  error: string | null;
  zoomIn: () => void;
  zoomOut: () => void;
  /** Omit the width to measure the scroll container itself. */
  fitToWidth: (containerWidth?: number) => void;
  /**
   * F1b (EDITOR-V2-PLAN §7 «Viewer»): paint `pageNumber` from a ONE-page PDF
   * instead of the document's own page (the server's draft preview), or back
   * from the document (`null`). The page box, the overlay and every
   * coordinate stay the document's: only the pixels change. At most
   * `MAX_PAGE_OVERRIDES` are kept (the oldest goes first); a new `source`
   * drops them all. Resolves `true` once the canvas really shows the new
   * pixels, `false` when a later call (or a new document) superseded it or
   * the bytes would not open.
   */
  setPageOverride: (pageNumber: number, bytes: ArrayBuffer | null) => Promise<boolean>;
  /** The pages painted from an override right now (identity changes with membership). */
  overriddenPages: ReadonlySet<number>;
  /**
   * The NEXT `source` replaces this one in place (a new version of the same
   * document): the zoom and the spot in the middle of the viewport are kept
   * instead of fitting and scrolling to the top.
   */
  holdViewForNextSource: () => void;
  /**
   * The median colour of the painted page under `rect` (PDF points, top-left,
   * UNROTATED -- a text block of the engine; ROTFIX: mapped onto the canvas,
   * which is painted at the page's own `/Rotate`), as `rgb(r, g, b)`; `null`
   * while the page holds no pixels.
   * What a temporary mask is painted with, so it is the paper's own colour.
   */
  sampleColor: (pageNumber: number, rect: { x: number; y: number; w: number; h: number }) => string | null;
}

/** Pages painted from a one-page preview at the same time (F1b). */
export const MAX_PAGE_OVERRIDES = 6;

interface PageOverride {
  id: number;
  doc: PDFDocumentProxy;
}

interface DocGeometry {
  sizes: Map<number, PageSize>;
  rotations: Map<number, number>;
}

const EMPTY_GEOMETRY: DocGeometry = { sizes: new Map(), rotations: new Map() };

interface ScaleAnchor {
  page: number;
  /** Where the viewport centre sat inside that page, 0 = top, 1 = bottom. */
  fraction: number;
}

export function usePdfViewer(source: ArrayBuffer | null): PdfViewerState {
  const containerRef = useRef<HTMLDivElement>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);

  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPageState] = useState(1);
  const [scale, setScaleState] = useState(1.25);
  const [geometry, setGeometry] = useState<DocGeometry>(EMPTY_GEOMETRY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Reader-applied quarter turns, per page. Absent means 0; replaced whole
   *  so its identity is the signal that something has to be repainted. */
  const [viewRotations, setViewRotations] = useState<ReadonlyMap<number, Quarter>>(
    () => new Map(),
  );
  /** The render window as React state (plan §3): only replaced when it changes. */
  const [windowPages, setWindowPages] = useState<ReadonlySet<number>>(() => new Set());

  // --- imperative state -----------------------------------------------------
  // Scrolling must not re-render the document for every frame, so everything
  // the renderer needs lives in refs and is read, never subscribed to.
  const pageElsRef = useRef(new Map<number, HTMLDivElement>());
  const canvasesRef = useRef(new Map<number, HTMLCanvasElement>());
  /** page -> `renderKey` of what its canvas currently holds (scale + turn). */
  const paintedRef = useRef(new Map<number, string>());
  const tasksRef = useRef(new Map<number, CancellableRender>());
  const queueRef = useRef<number[]>([]);
  const activeRef = useRef(0);
  const visibleRef = useRef(new Set<number>());
  const windowRef = useRef(new Set<number>());
  const observerRef = useRef<IntersectionObserver | null>(null);
  const scheduleFrameRef = useRef(0);
  const anchorRef = useRef<ScaleAnchor | null>(null);
  /** True while the scale should follow the container width. */
  const fitModeRef = useRef(true);
  const lastFitWidthRef = useRef(0);
  const initialFitRef = useRef(false);

  // Mirrors of the reactive values, for the imperative paths above.
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const currentPageRef = useRef(currentPage);
  currentPageRef.current = currentPage;
  const pageCountRef = useRef(pageCount);
  pageCountRef.current = pageCount;
  const geometryRef = useRef(geometry);
  geometryRef.current = geometry;
  const rotationsRef = useRef(viewRotations);
  rotationsRef.current = viewRotations;

  /** Reader-applied turn for a page, from the imperative paths. */
  const turnOf = useCallback(
    (pageNumber: number): Quarter => rotationsRef.current.get(pageNumber) ?? 0,
    [],
  );

  /* -- F1b: one-page overrides (the draft preview) ----------------------------- */
  const overridesRef = useRef(new Map<number, PageOverride>());
  const overrideSeqRef = useRef(0);
  /** page -> the override id the latest `setPageOverride` asked for (0 = none). */
  const wantedOverrideRef = useRef(new Map<number, number>());
  const overrideWaitersRef = useRef(new Map<number, Array<{ id: number; resolve: (ok: boolean) => void }>>());
  const [overriddenPages, setOverriddenPages] = useState<ReadonlySet<number>>(() => new Set());
  const overrideIdOf = (pageNumber: number): number => overridesRef.current.get(pageNumber)?.id ?? 0;

  /** What a canvas is holding. Both halves matter: a page repaints when the
   *  zoom moves **and** when the reader turns it -- and (F1b) when its
   *  pixels come from another one-page document. */
  const renderKey = (targetScale: number, turn: Quarter, override = 0): string =>
    `${targetScale}|${turn}|${override}`;

  // --- painting -------------------------------------------------------------

  const releasePage = useCallback((pageNumber: number) => {
    tasksRef.current.get(pageNumber)?.cancel();
    tasksRef.current.delete(pageNumber);
    const canvas = canvasesRef.current.get(pageNumber);
    if (canvas) {
      // Zeroing both dimensions is what actually frees the bitmap; a
      // `clearRect` only paints over memory that stays allocated.
      canvas.width = 0;
      canvas.height = 0;
    }
    paintedRef.current.delete(pageNumber);
    pageElsRef.current.get(pageNumber)?.removeAttribute('data-rendered');
  }, []);

  const releaseAll = useCallback(() => {
    [...tasksRef.current.keys()].forEach((pageNumber) => releasePage(pageNumber));
    [...canvasesRef.current.keys()].forEach((pageNumber) => releasePage(pageNumber));
    queueRef.current = [];
    windowRef.current = new Set();
  }, [releasePage]);

  /** The band of pages that may hold pixels right now. */
  const computeWindow = useCallback((): Set<number> => {
    const total = pageCountRef.current;
    const wanted = new Set<number>();
    if (total === 0) return wanted;

    const focus = Math.min(Math.max(1, currentPageRef.current), total);
    let first = focus;
    let last = focus;
    if (visibleRef.current.size > 0) {
      first = Math.min(...visibleRef.current);
      last = Math.max(...visibleRef.current);
    }
    first = Math.max(1, first - RENDER_MARGIN_PAGES);
    last = Math.min(total, last + RENDER_MARGIN_PAGES);

    // Zoomed far out the visible band alone can be longer than the ceiling;
    // then keep the pages closest to the one the user is looking at.
    const ordered: number[] = [];
    for (let pageNumber = first; pageNumber <= last; pageNumber += 1) ordered.push(pageNumber);
    ordered.sort((a, b) => Math.abs(a - focus) - Math.abs(b - focus) || a - b);
    ordered.slice(0, MAX_RENDERED_PAGES).forEach((pageNumber) => wanted.add(pageNumber));
    return wanted;
  }, []);

  // `pump` starts renders and every render calls `pump` again when it ends, so
  // one of the two has to reach the other through a ref.
  const paintPageRef = useRef<((pageNumber: number) => Promise<void>) | null>(null);

  const pump = useCallback(() => {
    while (activeRef.current < MAX_ACTIVE_RENDERS && queueRef.current.length > 0) {
      const next = queueRef.current.shift();
      if (next === undefined) return;
      void paintPageRef.current?.(next);
    }
  }, []);

  const paintPage = useCallback(
    async (pageNumber: number) => {
      const doc = documentRef.current;
      if (!doc || !canvasesRef.current.has(pageNumber)) return;
      const target = scaleRef.current;
      const turn = turnOf(pageNumber);
      const override = overridesRef.current.get(pageNumber) ?? null;
      const key = renderKey(target, turn, override?.id ?? 0);
      activeRef.current += 1;
      let task: CancellableRender | null = null;
      try {
        // F1b: an overridden page is page 1 of its own one-page document.
        const page = override ? await override.doc.getPage(1) : await doc.getPage(pageNumber);
        // The document, the zoom, the turn or the scroll position may all have
        // moved on while the page was being fetched.
        if (documentRef.current !== doc) return;
        if (renderKey(scaleRef.current, turnOf(pageNumber), overrideIdOf(pageNumber)) !== key) return;
        if (!windowRef.current.has(pageNumber)) return;
        const canvas = canvasesRef.current.get(pageNumber);
        if (!canvas) return;
        const context = canvas.getContext('2d');
        if (!context) return;

        // Render at device resolution, present at CSS size (the canvas is
        // stretched over its page by CSS), so overlay maths stays in CSS pixels
        // regardless of screen density -- see `lib/viewport.ts`.
        //
        // The reader's turn is added to the page's own `/Rotate` and handed to
        // pdf.js, so the bitmap really is drawn turned -- no CSS transform on
        // the canvas, no resampling, text stays as crisp as it was.
        const ratio = Math.min(window.devicePixelRatio || 1, MAX_DEVICE_PIXEL_RATIO);
        const viewport = page.getViewport({
          scale: target * ratio,
          rotation: (page.rotate ?? 0) + turn,
        });

        // Cancel BEFORE resizing: writing `canvas.width` wipes a bitmap a live
        // task is still painting into, which is what used to make pages flash.
        tasksRef.current.get(pageNumber)?.cancel();
        canvas.width = Math.max(1, Math.floor(viewport.width));
        canvas.height = Math.max(1, Math.floor(viewport.height));

        task = page.render({ canvasContext: context, viewport }) as CancellableRender;
        tasksRef.current.set(pageNumber, task);
        await task.promise;
        if (documentRef.current !== doc) return;
        paintedRef.current.set(pageNumber, key);
        const element = pageElsRef.current.get(pageNumber);
        element?.setAttribute('data-rendered', 'true');
        if (override) element?.setAttribute('data-override', String(override.id));
        else element?.removeAttribute('data-override');
        const waiting = overrideWaitersRef.current.get(pageNumber);
        if (waiting) {
          const painted = override?.id ?? 0;
          const keep = waiting.filter((waiter) => {
            if (waiter.id !== painted) return true;
            waiter.resolve(true);
            return false;
          });
          if (keep.length) overrideWaitersRef.current.set(pageNumber, keep);
          else overrideWaitersRef.current.delete(pageNumber);
        }
      } catch (cause) {
        const name = (cause as { name?: string }).name;
        // A cancelled render is the normal way a fast scroll ends, and a
        // destroyed document rejects everything still in flight.
        if (name !== 'RenderingCancelledException' && documentRef.current === doc) {
          setError((current) =>
            current ?? (cause instanceof Error ? cause.message : 'Failed to render page'),
          );
        }
      } finally {
        // A cancelled render rejects, so the bookkeeping has to be cleared
        // here: leaving the task behind would make this page look "busy"
        // forever and it would never be queued again.
        if (task && tasksRef.current.get(pageNumber) === task) {
          tasksRef.current.delete(pageNumber);
        }
        activeRef.current -= 1;
        pump();
      }
    },
    [pump, turnOf],
  );
  paintPageRef.current = paintPage;

  /** Off to the side of the window/queue machinery above: see the interface. */
  const renderPageTo = useCallback(
    async (pageNumber: number, canvas: HTMLCanvasElement, cssWidth: number): Promise<boolean> => {
      const doc = documentRef.current;
      if (!doc || cssWidth <= 0) return false;
      if (pageNumber < 1 || pageNumber > doc.numPages) return false;
      try {
        const page = await doc.getPage(pageNumber);
        if (documentRef.current !== doc) return false;
        const context = canvas.getContext('2d');
        if (!context) return false;

        // The thumbnail follows the reader's turn, so the strip is a picture
        // of what the document actually looks like right now.
        const rotation = (page.rotate ?? 0) + turnOf(pageNumber);
        const unscaled = page.getViewport({ scale: 1, rotation });
        if (unscaled.width <= 0) return false;
        const ratio = Math.min(window.devicePixelRatio || 1, MAX_DEVICE_PIXEL_RATIO);
        const viewport = page.getViewport({
          scale: (cssWidth / unscaled.width) * ratio,
          rotation,
        });

        canvas.width = Math.max(1, Math.floor(viewport.width));
        canvas.height = Math.max(1, Math.floor(viewport.height));
        // A PDF page is transparent wherever nothing is drawn; at thumbnail
        // size that reads as a hole rather than as paper.
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas.width, canvas.height);

        await page.render({ canvasContext: context, viewport }).promise;
        return documentRef.current === doc;
      } catch {
        // Deliberately silent -- see the interface.
        return false;
      }
    },
    [turnOf],
  );

  // --- page text ------------------------------------------------------------

  /** page -> its extracted text, in insertion order (that is the eviction order). */
  const textCacheRef = useRef(new Map<number, PageText>());
  /** In-flight extractions, so ten pages searched at once ask pdf.js once. */
  const textJobsRef = useRef(new Map<number, Promise<PageText | null>>());
  const textCharsRef = useRef(0);
  const textPiecesRef = useRef(0);

  /**
   * F2: one per document opened. A new document with the SAME page count (a
   * page moved in the draft) must still invalidate what the search found:
   * `getPageText` changes identity with it, and the search runs again.
   */
  const [docSeq, setDocSeq] = useState(0);

  const clearTextCache = useCallback(() => {
    textCacheRef.current.clear();
    textJobsRef.current.clear();
    textCharsRef.current = 0;
    textPiecesRef.current = 0;
  }, []);

  /** Trim to the two budgets, oldest page first, never the page just added. */
  const trimTextCache = useCallback((keep: number) => {
    const cache = textCacheRef.current;
    while (
      (textCharsRef.current > MAX_TEXT_CHARS || textPiecesRef.current > MAX_TEXT_PIECES) &&
      cache.size > 1
    ) {
      let victim = -1;
      for (const pageNumber of cache.keys()) {
        if (pageNumber !== keep) {
          victim = pageNumber;
          break;
        }
      }
      if (victim < 0) return;
      const dropped = cache.get(victim);
      cache.delete(victim);
      if (dropped) {
        textCharsRef.current -= dropped.text.length;
        textPiecesRef.current -= dropped.pieces.length;
      }
    }
  }, []);

  const getPageText = useCallback(
    (pageNumber: number): Promise<PageText | null> => {
      void docSeq; // the identity follows the document (see `docSeq`)
      const doc = documentRef.current;
      if (!doc || pageNumber < 1 || pageNumber > doc.numPages) return Promise.resolve(null);
      const cached = textCacheRef.current.get(pageNumber);
      if (cached) return Promise.resolve(cached);
      const running = textJobsRef.current.get(pageNumber);
      if (running) return running;

      const job = (async (): Promise<PageText | null> => {
        try {
          const page = await doc.getPage(pageNumber);
          if (documentRef.current !== doc) return null;
          const content = await page.getTextContent();
          if (documentRef.current !== doc) return null;

          // Default rotation: the page's own `/Rotate`, exactly like the
          // `pageSizes` entry this geometry has to agree with. The reader's
          // quarter turn is NOT applied -- a search mark is handed to the
          // overlay in unrotated points and the layer turns as a whole.
          // ROTFIX: measured unrotated (see `PageText.frame`).
          const own = normalizeQuarter(page.rotate ?? 0);
          const viewport = page.getViewport({ scale: 1, rotation: 0 });
          const frame: PageFrame | null = own === 0 ? null : { rotation: own, width: viewport.width, height: viewport.height };
          const pieces: TextPiece[] = [];
          let text = '';
          for (const item of content.items) {
            if (!('str' in item)) continue;
            const run = item.str;
            if (run.length > 0) {
              // `lib/textRuns.ts runGeometry` is the ONE place an item becomes
              // a box -- the text layer (edit-text) reads the very same one.
              // The search box hangs one font size above the baseline.
              //
              // Known limit: the box is axis-aligned, so a run that is itself
              // rotated (vertical text, or a page with its own `/Rotate`) gets
              // the right start and the wrong extent. A highlight is a pointer
              // to a word, not a measurement of it; the match and the page it
              // is on are still exactly right.
              const geometry = runGeometry(item, viewport.transform);
              pieces.push({
                start: text.length,
                length: run.length,
                x: geometry.x,
                y: geometry.top,
                width: geometry.width,
                height: geometry.fontSize,
                family: familyOf(
                  (content.styles as Record<string, TextStyleLike>)[item.fontName],
                  undefined,
                ),
              });
              text += run;
            }
            // A line break is a character in the page's text and belongs to no
            // piece, so a match that spans one simply draws two rectangles.
            if (item.hasEOL) text += '\n';
          }

          if (documentRef.current !== doc) return null;
          const result: PageText = { text, pieces, frame };
          textCacheRef.current.set(pageNumber, result);
          textCharsRef.current += text.length;
          textPiecesRef.current += pieces.length;
          trimTextCache(pageNumber);
          return result;
        } catch {
          // A page that will not parse is a page with no text, not an error
          // banner over a document that reads perfectly well.
          return null;
        } finally {
          textJobsRef.current.delete(pageNumber);
        }
      })();

      textJobsRef.current.set(pageNumber, job);
      return job;
    },
    [trimTextCache, docSeq],
  );

  /* -- the text layer (plan §3) ---------------------------------------------- */

  /** page -> raw `TextContent`, oldest first; ≤ `MAX_RAW_TEXT_PAGES`. */
  const rawTextRef = useRef(new Map<number, RawText>());
  const rawJobsRef = useRef(new Map<number, Promise<RawText | null>>());

  const clearRawText = useCallback(() => {
    rawTextRef.current.clear();
    rawJobsRef.current.clear();
  }, []);

  const getRawText = useCallback(
    (doc: PDFDocumentProxy, page: PDFPageProxy, pageNumber: number): Promise<RawText | null> => {
      const cached = rawTextRef.current.get(pageNumber);
      if (cached) {
        // Touch: most recently used goes to the back of the eviction order.
        rawTextRef.current.delete(pageNumber);
        rawTextRef.current.set(pageNumber, cached);
        return Promise.resolve(cached);
      }
      const running = rawJobsRef.current.get(pageNumber);
      if (running) return running;
      const job = page
        .getTextContent()
        .then((content): RawText | null => {
          if (documentRef.current !== doc) return null;
          const raw: RawText = {
            items: content.items as ReadonlyArray<TextItemLike | { type?: string }>,
            styles: content.styles as Record<string, TextStyleLike>,
            content,
          };
          const cache = rawTextRef.current;
          cache.set(pageNumber, raw);
          while (cache.size > MAX_RAW_TEXT_PAGES) {
            const oldest = cache.keys().next().value;
            if (oldest === undefined) break;
            cache.delete(oldest);
          }
          return raw;
        })
        .catch(() => null)
        .finally(() => {
          rawJobsRef.current.delete(pageNumber);
        });
      rawJobsRef.current.set(pageNumber, job);
      return job;
    },
    [],
  );

  /**
   * Bold / italic are not in `TextContent`: they are properties of the FONT,
   * which pdf.js hands to the main thread (`page.commonObjs`) while the page
   * is being painted. The text layer only lives on painted pages, so the font
   * is normally there already; if it is not, the paint is waited for, briefly,
   * and a font that never arrives is read as regular.
   */
  const readFaces = useCallback(
    async (page: PDFPageProxy, names: readonly string[]): Promise<Map<string, FontFaceInfo>> => {
      const faces = new Map<string, FontFaceInfo>();
      const objects = page.commonObjs as unknown as {
        has(id: string): boolean;
        get(id: string, callback?: (data: unknown) => void): unknown;
      };
      await Promise.all(
        names.map(async (name) => {
          let data: unknown = null;
          try {
            if (objects.has(name)) data = objects.get(name);
            else {
              data = await Promise.race([
                new Promise<unknown>((resolve) => {
                  objects.get(name, resolve);
                }),
                new Promise<null>((resolve) => window.setTimeout(() => resolve(null), FONT_WAIT_MS)),
              ]);
            }
          } catch {
            data = null;
          }
          const font = (data ?? {}) as { name?: string; bold?: boolean; italic?: boolean; black?: boolean };
          faces.set(name, faceFromName(font.name, font));
        }),
      );
      return faces;
    },
    [],
  );

  const renderTextLayer = useCallback(
    (pageNumber: number, container: HTMLDivElement): TextLayerTask => {
      let cancelled = false;
      let layer: pdfjs.TextLayer | null = null;
      const done = (async (): Promise<TextLayerHandle | null> => {
        const doc = documentRef.current;
        if (!doc || pageNumber < 1 || pageNumber > doc.numPages) return null;
        const page = await doc.getPage(pageNumber);
        if (cancelled || documentRef.current !== doc) return null;
        const raw = await getRawText(doc, page, pageNumber);
        if (!raw || cancelled || documentRef.current !== doc) return null;

        // The page's own `/Rotate`, never the reader's turn (a turned page has
        // no text layer at all). Scale 1: the spans are laid out in % of the
        // page and in `var(--scale-factor)`, which the caller owns.
        const viewport = page.getViewport({ scale: 1 });
        container.replaceChildren();
        layer = new pdfjs.TextLayer({
          textContentSource: raw.content as ConstructorParameters<typeof pdfjs.TextLayer>[0]['textContentSource'],
          container,
          viewport,
        });
        // pdf.js sizes the container to `round(down, --scale-factor × W, 1px)`;
        // the page box is exactly `W × scale`, so the layer simply fills it.
        container.style.width = '100%';
        container.style.height = '100%';
        await layer.render();
        if (cancelled || documentRef.current !== doc) return null;

        const names = new Set<string>();
        raw.items.forEach((item) => {
          if ('str' in item && item.str.length > 0) names.add((item as TextItemLike).fontName);
        });
        const faces = await readFaces(page, [...names]);
        if (cancelled || documentRef.current !== doc) return null;
        const runs = buildRuns(
          raw.items,
          raw.styles,
          viewport.transform,
          faces,
        );
        const drawn = layer;
        return {
          runs,
          spanIndexOf: (element: Element) => drawn.textDivs.indexOf(element as HTMLElement),
        };
      })().catch(() => null);

      return {
        cancel() {
          if (cancelled) return;
          cancelled = true;
          layer?.cancel();
          container.replaceChildren();
        },
        done,
      };
    },
    [getRawText, readFaces],
  );

  const runSchedule = useCallback(() => {
    const wanted = computeWindow();
    windowRef.current = wanted;
    // React hears about the window only when its membership changes.
    setWindowPages((current) =>
      current.size === wanted.size && [...wanted].every((pageNumber) => current.has(pageNumber))
        ? current
        : new Set(wanted),
    );

    // Hand back everything that scrolled away. The canvas itself is the
    // authority on whether there is memory to release: a render cancelled
    // mid-flight has already sized its bitmap without ever counting as painted.
    [...canvasesRef.current.entries()].forEach(([pageNumber, canvas]) => {
      if (wanted.has(pageNumber)) return;
      if (canvas.width > 0 || canvas.height > 0 || tasksRef.current.has(pageNumber)) {
        releasePage(pageNumber);
      }
    });

    if (!documentRef.current) {
      queueRef.current = [];
      return;
    }
    const target = scaleRef.current;
    const focus = currentPageRef.current;
    queueRef.current = [...wanted]
      .filter(
        (pageNumber) =>
          canvasesRef.current.has(pageNumber) &&
          paintedRef.current.get(pageNumber) !==
            renderKey(target, turnOf(pageNumber), overrideIdOf(pageNumber)) &&
          !tasksRef.current.has(pageNumber),
      )
      .sort((a, b) => Math.abs(a - focus) - Math.abs(b - focus) || a - b);
    pump();
  }, [computeWindow, pump, releasePage, turnOf]);

  /** Coalesces the bursts that scrolling, mounting and zooming all produce. */
  const scheduleRender = useCallback(() => {
    if (scheduleFrameRef.current) return;
    scheduleFrameRef.current = window.requestAnimationFrame(() => {
      scheduleFrameRef.current = 0;
      runSchedule();
    });
  }, [runSchedule]);

  // --- F1b: page overrides ----------------------------------------------------

  const publishOverrides = useCallback(() => {
    setOverriddenPages(new Set(overridesRef.current.keys()));
  }, []);

  const settleWaiters = useCallback((pageNumber: number, ok: boolean, exceptId?: number) => {
    const waiting = overrideWaitersRef.current.get(pageNumber);
    if (!waiting) return;
    const keep = waiting.filter((waiter) => {
      if (exceptId !== undefined && waiter.id === exceptId) return true;
      waiter.resolve(ok);
      return false;
    });
    if (keep.length) overrideWaitersRef.current.set(pageNumber, keep);
    else overrideWaitersRef.current.delete(pageNumber);
  }, []);

  /** Repaint one page now (its key changed): drop what is in flight for it. */
  const repaint = useCallback(
    (pageNumber: number) => {
      tasksRef.current.get(pageNumber)?.cancel();
      tasksRef.current.delete(pageNumber);
      paintedRef.current.delete(pageNumber);
      scheduleRender();
    },
    [scheduleRender],
  );

  const dropOverride = useCallback((pageNumber: number) => {
    const current = overridesRef.current.get(pageNumber);
    if (!current) return false;
    overridesRef.current.delete(pageNumber);
    void current.doc.destroy();
    return true;
  }, []);

  const setPageOverride = useCallback(
    (pageNumber: number, bytes: ArrayBuffer | null): Promise<boolean> => {
      const baseDoc = documentRef.current;
      overrideSeqRef.current += 1;
      const id = overrideSeqRef.current;
      // Whatever an earlier call for this page was waiting for is superseded.
      settleWaiters(pageNumber, false);
      if (!bytes) {
        wantedOverrideRef.current.delete(pageNumber);
        if (dropOverride(pageNumber)) {
          publishOverrides();
          return new Promise<boolean>((resolve) => {
            overrideWaitersRef.current.set(pageNumber, [{ id: 0, resolve }]);
            repaint(pageNumber);
            if (!windowRef.current.has(pageNumber)) settleWaiters(pageNumber, true);
          });
        }
        return Promise.resolve(true);
      }
      wantedOverrideRef.current.set(pageNumber, id);
      return pdfjs
        .getDocument({ data: bytes.slice(0) })
        .promise.then((doc) => {
          if (documentRef.current !== baseDoc || wantedOverrideRef.current.get(pageNumber) !== id) {
            void doc.destroy();
            return false;
          }
          dropOverride(pageNumber);
          overridesRef.current.set(pageNumber, { id, doc });
          // The oldest override goes first; its page is painted from the document again.
          while (overridesRef.current.size > MAX_PAGE_OVERRIDES) {
            const oldest = overridesRef.current.keys().next().value as number | undefined;
            if (oldest === undefined || oldest === pageNumber) break;
            dropOverride(oldest);
            wantedOverrideRef.current.delete(oldest);
            repaint(oldest);
          }
          publishOverrides();
          const painted = new Promise<boolean>((resolve) => {
            const list = overrideWaitersRef.current.get(pageNumber) ?? [];
            list.push({ id, resolve });
            overrideWaitersRef.current.set(pageNumber, list);
          });
          repaint(pageNumber);
          // A page outside the render window is not painted until it is back:
          // the override is in place, which is all the caller can wait for.
          if (!windowRef.current.has(pageNumber)) settleWaiters(pageNumber, true);
          return painted;
        })
        .catch(() => false);
    },
    [dropOverride, publishOverrides, repaint, settleWaiters],
  );

  const clearOverrides = useCallback(() => {
    [...overridesRef.current.keys()].forEach((pageNumber) => {
      dropOverride(pageNumber);
      settleWaiters(pageNumber, false);
    });
    wantedOverrideRef.current.clear();
    setOverriddenPages((current) => (current.size === 0 ? current : new Set()));
  }, [dropOverride, settleWaiters]);

  const sampleColor = useCallback(
    (pageNumber: number, rect: { x: number; y: number; w: number; h: number }): string | null => {
      const canvas = canvasesRef.current.get(pageNumber);
      const size = geometryRef.current.sizes.get(pageNumber);
      if (!canvas || !size || canvas.width === 0 || turnOf(pageNumber) !== 0) return null;
      const context = canvas.getContext('2d');
      if (!context) return null;
      // ROTFIX: the rect is the engine's (unrotated); the canvas is the page as displayed.
      const own = normalizeQuarter(geometryRef.current.rotations.get(pageNumber) ?? 0);
      if (own !== 0) {
        const flat = swapsAxes(own) ? { width: size.height, height: size.width } : size;
        const shown = mapRectPt(rect, { rotation: own, width: flat.width, height: flat.height }, UNROTATED, own);
        rect = shown;
      }
      const k = canvas.width / size.width;
      const x0 = Math.max(0, Math.floor(rect.x * k));
      const y0 = Math.max(0, Math.floor(rect.y * k));
      const x1 = Math.min(canvas.width, Math.ceil((rect.x + rect.w) * k));
      const y1 = Math.min(canvas.height, Math.ceil((rect.y + rect.h) * k));
      if (x1 - x0 < 1 || y1 - y0 < 1) return null;
      let data: Uint8ClampedArray;
      try {
        data = context.getImageData(x0, y0, x1 - x0, y1 - y0).data;
      } catch {
        return null;
      }
      const reds: number[] = [];
      const greens: number[] = [];
      const blues: number[] = [];
      // Every pixel up to ~40 000, then a stride: a median needs no more.
      const stride = Math.max(1, Math.floor(data.length / 4 / 40000));
      for (let at = 0; at < data.length; at += 4 * stride) {
        if (data[at + 3] === 0) continue;
        reds.push(data[at]);
        greens.push(data[at + 1]);
        blues.push(data[at + 2]);
      }
      if (reds.length === 0) return null;
      const median = (values: number[]) => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
      return `rgb(${median(reds)}, ${median(greens)}, ${median(blues)})`;
    },
    [turnOf],
  );

  /** Set by `holdViewForNextSource`: the view to put back once the next document is laid out. */
  const heldViewRef = useRef<ScaleAnchor | null>(null);
  const restoreViewRef = useRef<ScaleAnchor | null>(null);

  // --- where we are ---------------------------------------------------------

  /** The page with the most of itself on screen wins; ties go to the lower one. */
  const updateCurrentPage = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const bounds = container.getBoundingClientRect();
    const viewTop = bounds.top;
    const viewBottom = bounds.top + container.clientHeight;

    const candidates =
      visibleRef.current.size > 0 ? [...visibleRef.current] : [...pageElsRef.current.keys()];
    candidates.sort((a, b) => a - b);

    let bestPage = 0;
    let bestOverlap = 0;
    candidates.forEach((pageNumber) => {
      const element = pageElsRef.current.get(pageNumber);
      if (!element) return;
      const rect = element.getBoundingClientRect();
      const overlap = Math.min(rect.bottom, viewBottom) - Math.max(rect.top, viewTop);
      if (overlap > bestOverlap + 0.5) {
        bestOverlap = overlap;
        bestPage = pageNumber;
      }
    });

    if (bestPage > 0 && bestPage !== currentPageRef.current) {
      currentPageRef.current = bestPage;
      setCurrentPageState(bestPage);
    }
  }, []);

  const scrollToPage = useCallback((page: number) => {
    const total = Math.max(1, pageCountRef.current);
    const target = Math.min(Math.max(1, Math.round(page)), total);
    currentPageRef.current = target;
    setCurrentPageState(target);
    const container = containerRef.current;
    const element = pageElsRef.current.get(target);
    if (!container || !element) return;
    const delta =
      element.getBoundingClientRect().top -
      container.getBoundingClientRect().top -
      SCROLL_MARGIN_PX;
    container.scrollTop += delta;
  }, []);

  const revealPoint = useCallback((page: number, point: Point) => {
    const total = Math.max(1, pageCountRef.current);
    const target = Math.min(Math.max(1, Math.round(page)), total);
    currentPageRef.current = target;
    setCurrentPageState(target);
    const container = containerRef.current;
    const element = pageElsRef.current.get(target);
    if (!container || !element) return;

    const size = geometryRef.current.sizes.get(target);
    const factor = scaleRef.current;
    // The caller speaks unrotated points; the box on screen may be turned, so
    // the same affine map the overlay layer uses puts the spot where the eye
    // will actually find it.
    const local = size
      ? rotatePointPx(
          { x: point.x * factor, y: point.y * factor },
          turnOf(target),
          size.width * factor,
          size.height * factor,
        )
      : { x: point.x * factor, y: point.y * factor };

    const pageBox = element.getBoundingClientRect();
    const viewBox = container.getBoundingClientRect();
    container.scrollTop +=
      pageBox.top - viewBox.top + local.y - container.clientHeight * REVEAL_FRACTION;

    // Sideways only when the spot is really outside: a zoomed-out document
    // must not be yanked left every time the reader steps to the next match.
    const x = pageBox.left - viewBox.left + local.x;
    if (x < 0 || x > container.clientWidth) {
      container.scrollLeft += x - container.clientWidth / 2;
    }
  }, [turnOf]);

  // --- rotation (view only) ---------------------------------------------------

  const rotatePage = useCallback((pageNumber: number, delta = 90) => {
    const step = normalizeQuarter(delta);
    setViewRotations((current) => {
      const next = new Map(current);
      const turned = normalizeQuarter((current.get(pageNumber) ?? 0) + step);
      if (turned === 0) next.delete(pageNumber);
      else next.set(pageNumber, turned);
      return next;
    });
  }, []);

  const rotationOf = useCallback(
    (pageNumber: number): Quarter => viewRotations.get(pageNumber) ?? 0,
    [viewRotations],
  );

  // --- zoom -----------------------------------------------------------------

  /** Where the viewport centre is, in page-local terms, before the scale moves. */
  const readAnchor = useCallback((): ScaleAnchor | null => {
    const container = containerRef.current;
    if (!container) return null;
    const bounds = container.getBoundingClientRect();
    const centre = bounds.top + container.clientHeight / 2;
    const pages = [...pageElsRef.current.keys()].sort((a, b) => a - b);
    for (const pageNumber of pages) {
      const element = pageElsRef.current.get(pageNumber);
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      if (rect.height <= 0) continue;
      if (centre >= rect.top && centre <= rect.bottom) {
        return { page: pageNumber, fraction: (centre - rect.top) / rect.height };
      }
    }
    return { page: currentPageRef.current, fraction: 0.5 };
  }, []);

  const holdViewForNextSource = useCallback(() => {
    heldViewRef.current = readAnchor();
  }, [readAnchor]);

  const applyScale = useCallback(
    (
      next: number | ((current: number) => number),
      options?: { manual?: boolean; anchor?: boolean },
    ) => {
      if (options?.manual) fitModeRef.current = false;
      anchorRef.current = options?.anchor === false ? null : readAnchor();
      setScaleState((current) => {
        const raw = typeof next === 'function' ? next(current) : next;
        if (!Number.isFinite(raw)) return current;
        return Math.max(MIN_SCALE, Math.min(raw, MAX_SCALE));
      });
    },
    [readAnchor],
  );

  const applyFit = useCallback(
    (containerWidth?: number, options?: { anchor?: boolean }) => {
      const width =
        containerWidth && containerWidth > 0
          ? containerWidth
          : (containerRef.current?.clientWidth ?? 0);
      if (width <= 0) return;
      let widest = 0;
      geometryRef.current.sizes.forEach((size, pageNumber) => {
        // A turned page is as wide as it is tall; fitting to the unrotated
        // width would push it off the side of the stage.
        const laid = rotatedSize(size, turnOf(pageNumber));
        if (laid.width > widest) widest = laid.width;
      });
      if (widest <= 0) return;
      lastFitWidthRef.current = width;
      applyScale(() => (width - FIT_PADDING_PX) / widest, { anchor: options?.anchor !== false });
    },
    [applyScale, turnOf],
  );

  const zoomIn = useCallback(
    () => applyScale((value) => value + SCALE_STEP, { manual: true }),
    [applyScale],
  );
  const zoomOut = useCallback(
    () => applyScale((value) => value - SCALE_STEP, { manual: true }),
    [applyScale],
  );
  const setScale = useCallback(
    (value: number) => applyScale(value, { manual: true }),
    [applyScale],
  );
  const fitToWidth = useCallback(
    (containerWidth?: number) => {
      fitModeRef.current = true;
      applyFit(containerWidth);
    },
    [applyFit],
  );

  // --- registration ---------------------------------------------------------

  const registerPage = useCallback((pageNumber: number, element: HTMLDivElement | null) => {
    const previous = pageElsRef.current.get(pageNumber);
    if (previous && previous !== element) observerRef.current?.unobserve(previous);
    if (element) {
      pageElsRef.current.set(pageNumber, element);
      observerRef.current?.observe(element);
    } else {
      pageElsRef.current.delete(pageNumber);
      visibleRef.current.delete(pageNumber);
    }
  }, []);

  const registerCanvas = useCallback(
    (pageNumber: number, canvas: HTMLCanvasElement | null) => {
      if (canvas) {
        // A fresh canvas is 300 x 150 whether anyone asked for it or not.
        // Emptying it here costs nothing and makes `canvas.width > 0` mean
        // exactly one thing: this page is holding a bitmap.
        canvas.width = 0;
        canvas.height = 0;
        canvasesRef.current.set(pageNumber, canvas);
        scheduleRender();
        return;
      }
      tasksRef.current.get(pageNumber)?.cancel();
      tasksRef.current.delete(pageNumber);
      canvasesRef.current.delete(pageNumber);
      paintedRef.current.delete(pageNumber);
      pageElsRef.current.get(pageNumber)?.removeAttribute('data-rendered');
    },
    [scheduleRender],
  );

  // --- the document ---------------------------------------------------------

  useEffect(() => {
    // F1b: a new VERSION of the same document keeps the reader where they were.
    const held = heldViewRef.current;
    heldViewRef.current = null;
    releaseAll();
    clearOverrides();
    paintedRef.current.clear();
    visibleRef.current.clear();
    clearTextCache();
    clearRawText();
    setWindowPages(new Set());
    if (held) {
      restoreViewRef.current = held;
      // The zoom stays; no first-sight fit.
      initialFitRef.current = true;
    } else {
      restoreViewRef.current = null;
      fitModeRef.current = true;
      initialFitRef.current = false;
      lastFitWidthRef.current = 0;
    }
    setPageCount(0);
    setGeometry(EMPTY_GEOMETRY);
    // A turn belongs to the document it was applied to.
    setViewRotations(new Map());
    rotationsRef.current = new Map();
    if (!held) {
      setCurrentPageState(1);
      currentPageRef.current = 1;
      if (containerRef.current) containerRef.current.scrollTop = 0;
    }

    if (!source) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    // pdf.js transfers the buffer, so hand it a copy the caller can reuse.
    const task = pdfjs.getDocument({ data: source.slice(0) });
    task.promise
      .then(async (doc) => {
        if (cancelled) {
          void doc.destroy();
          return;
        }
        documentRef.current = doc;
        setPageCount(doc.numPages);
        // Every page's size up front: a placeholder can only hold the right
        // amount of space if it knows how big its page is, and the sizes are
        // mixed in real contracts (612x792 next to 612x817).
        const entries = await Promise.all(
          Array.from({ length: doc.numPages }, (_, index) =>
            doc.getPage(index + 1).then((page) => {
              const viewport = page.getViewport({ scale: 1 });
              return {
                pageNumber: index + 1,
                size: { width: viewport.width, height: viewport.height },
                rotation: page.rotate ?? 0,
              };
            }),
          ),
        );
        if (cancelled || documentRef.current !== doc) return;
        setDocSeq((value) => value + 1);
        setGeometry({
          sizes: new Map(entries.map((entry) => [entry.pageNumber, entry.size])),
          rotations: new Map(entries.map((entry) => [entry.pageNumber, entry.rotation])),
        });
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Failed to open PDF');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      releaseAll();
      clearOverrides();
      clearTextCache();
      clearRawText();
      documentRef.current = null;
      void task.destroy();
    };
  }, [source, releaseAll, clearTextCache, clearRawText, clearOverrides]);

  // F1b: the held view, once the new version's pages have their boxes.
  useLayoutEffect(() => {
    const anchor = restoreViewRef.current;
    if (!anchor || geometry.sizes.size === 0) return;
    restoreViewRef.current = null;
    const container = containerRef.current;
    const element = pageElsRef.current.get(Math.min(anchor.page, geometry.sizes.size));
    if (!container || !element) return;
    const bounds = container.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const centre = bounds.top + container.clientHeight / 2;
    container.scrollTop += rect.top + anchor.fraction * rect.height - centre;
    updateCurrentPage();
  }, [geometry, updateCurrentPage]);

  // --- observers ------------------------------------------------------------

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const pageNumber = Number((entry.target as HTMLElement).dataset.pageNumber);
          if (!pageNumber) return;
          if (entry.isIntersecting) visibleRef.current.add(pageNumber);
          else visibleRef.current.delete(pageNumber);
        });
        updateCurrentPage();
        scheduleRender();
      },
      { root: container, rootMargin: '0px', threshold: 0 },
    );
    observerRef.current = observer;
    // Pages register through a ref callback, which runs before this effect on
    // the first mount, so pick up whatever is already there.
    pageElsRef.current.forEach((element) => observer.observe(element));

    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, [scheduleRender, updateCurrentPage]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        updateCurrentPage();
      });
    };
    container.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      container.removeEventListener('scroll', onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [updateCurrentPage]);

  // A narrower pane (or a window resize) has to re-fit, or the page keeps the
  // width it was given when the document opened.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (!fitModeRef.current) return;
      const width = container.clientWidth;
      if (width <= 0 || Math.abs(width - lastFitWidthRef.current) < 1) return;
      applyFit(width);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [applyFit]);

  // First sight of a document: fit it, without the scroll anchoring that a
  // deliberate zoom wants.
  useEffect(() => {
    if (geometry.sizes.size === 0 || initialFitRef.current) return;
    initialFitRef.current = true;
    applyFit(containerRef.current?.clientWidth ?? 0, { anchor: false });
    if (containerRef.current) containerRef.current.scrollTop = 0;
  }, [geometry, applyFit]);

  // Zooming keeps the point that was in the middle of the viewport in the
  // middle of the viewport. Layout effect: the new page boxes are committed,
  // the browser has not painted yet, so the correction is invisible.
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    anchorRef.current = null;
    if (!anchor) return;
    const container = containerRef.current;
    const element = pageElsRef.current.get(anchor.page);
    if (!container || !element) return;
    const bounds = container.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const centre = bounds.top + container.clientHeight / 2;
    container.scrollTop += rect.top + anchor.fraction * rect.height - centre;
    updateCurrentPage();
  }, [scale, updateCurrentPage]);

  // Anything that changes what should be on screen re-runs the scheduler --
  // a turn included, which is why `viewRotations` is replaced whole.
  useEffect(() => {
    scheduleRender();
  }, [scale, pageCount, geometry, currentPage, viewRotations, scheduleRender]);

  // A turned page is a different shape, so the document has to be re-fitted or
  // a landscape page hangs off the side of the stage.
  useEffect(() => {
    if (!fitModeRef.current || geometry.sizes.size === 0) return;
    applyFit(containerRef.current?.clientWidth ?? 0, { anchor: false });
    // `applyFit` only reads refs, so this fires for the turn and nothing else.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewRotations]);

  useEffect(
    () => () => {
      if (scheduleFrameRef.current) window.cancelAnimationFrame(scheduleFrameRef.current);
      // Clearing the id matters as much as cancelling it: StrictMode unmounts
      // and remounts every effect, and a leftover id would make `scheduleRender`
      // think a frame it had cancelled was still on its way -- after which
      // nothing would ever be scheduled again.
      scheduleFrameRef.current = 0;
    },
    [],
  );

  const sizeOf = useCallback(
    (pageNumber: number): PageSize => {
      const size = geometry.sizes.get(pageNumber);
      if (!size) return { width: 0, height: 0 };
      return rotatedSize(
        { width: size.width * scale, height: size.height * scale },
        viewRotations.get(pageNumber) ?? 0,
      );
    },
    [geometry, scale, viewRotations],
  );

  const frameOf = useCallback(
    (pageNumber: number): PageFrame | null => {
      const size = geometry.sizes.get(pageNumber);
      if (!size) return null;
      const own = normalizeQuarter(geometry.rotations.get(pageNumber) ?? 0);
      // `sizes` is the page as displayed (its own /Rotate applied): undo the swap.
      return swapsAxes(own)
        ? { rotation: own, width: size.height, height: size.width }
        : { rotation: own, width: size.width, height: size.height };
    },
    [geometry],
  );

  const rotation = geometry.rotations.get(currentPage) ?? 0;
  const viewRotation: Quarter = viewRotations.get(currentPage) ?? 0;
  const anyRotated = viewRotations.size > 0;
  const pageSizes = geometry.sizes as ReadonlyMap<number, PageSize>;

  return useMemo<PdfViewerState>(
    () => ({
      containerRef,
      registerPage,
      registerCanvas,
      pageCount,
      currentPage,
      scrollToPage,
      revealPoint,
      scale,
      setScale,
      sizeOf,
      renderPageTo,
      getPageText,
      windowPages,
      renderTextLayer,
      rotation,
      frameOf,
      rotationOf,
      viewRotation,
      anyRotated,
      rotatePage,
      pageSizes,
      loading,
      error,
      zoomIn,
      zoomOut,
      fitToWidth,
      setPageOverride,
      overriddenPages,
      holdViewForNextSource,
      sampleColor,
    }),
    [
      setPageOverride,
      overriddenPages,
      holdViewForNextSource,
      sampleColor,
      registerPage,
      registerCanvas,
      pageCount,
      currentPage,
      scrollToPage,
      revealPoint,
      scale,
      setScale,
      sizeOf,
      renderPageTo,
      getPageText,
      windowPages,
      renderTextLayer,
      rotation,
      frameOf,
      rotationOf,
      viewRotation,
      anyRotated,
      rotatePage,
      pageSizes,
      loading,
      error,
      zoomIn,
      zoomOut,
      fitToWidth,
    ],
  );
}
