import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PdfViewerState } from '../hooks/usePdfViewer';
import { rotatedSize } from '../lib/viewport';

/**
 * The page strip beside the document.
 *
 * It is a *navigation* control, not a second viewer: it draws out of the
 * document `usePdfViewer` already has open (`renderPageTo`), never opens the
 * file again, and is bounded by exactly the same reasoning as the main column.
 * A 66-page contract with every thumbnail painted would be ~20 MB of bitmap for
 * pictures nobody is looking at, so:
 *
 *  * an `IntersectionObserver` (root: this strip) says which thumbnails are
 *    near the visible part of the strip, and only those are queued;
 *  * at most `MAX_DRAWN` canvases hold pixels at once -- the ones furthest from
 *    the page the user is on are handed back first;
 *  * one thumbnail paints at a time, nearest page first, so the strip never
 *    competes with the document for the pdf.js worker.
 *
 * A canvas is emptied by setting `width`/`height` to 0; `clearRect` would paint
 * over memory that stays allocated.
 */

/** CSS width of one thumbnail. Height follows the page's own aspect ratio. */
const THUMB_WIDTH = 120;
/** Ceiling on thumbnails holding pixels. ~2.5 strip-fuls on a tall screen. */
const MAX_DRAWN = 14;
/** Draw a little before a thumbnail scrolls in, so it is rarely seen empty. */
const LOOKAHEAD = '240px';

/**
 * F2 (EDITOR-V2-PLAN §7.4 «Sahifalar»): the page tools on the strip -- only
 * where the server has the document editor and this user may edit it. Real
 * changes of the document (saved with it), unlike the viewer's ⟳ which only
 * turns the view.
 */
export interface PageTools {
  /** «Insert page here» after `position` (0 = before the first page). */
  onInsertAfter: (position: number) => void;
  onDelete: (position: number) => void;
  onRotate: (position: number, delta: 90 | -90) => void;
  /** Move the page at `from` so it follows the page now at `toAfter` (0 = first). */
  onMove: (from: number, toAfter: number) => void;
  /** The page's absolute rotation in the draft. */
  rotationOf: (position: number) => number;
  busy: boolean;
  error: string | null;
}

interface Props {
  viewer: PdfViewerState;
  tools?: PageTools | null;
}

export function PdfThumbnails({ viewer, tools = null }: Props) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const { pageCount, pageSizes, currentPage, scrollToPage, renderPageTo, rotationOf } = viewer;

  const listRef = useRef<HTMLDivElement>(null);
  const itemsRef = useRef(new Map<number, HTMLButtonElement>());
  const canvasesRef = useRef(new Map<number, HTMLCanvasElement>());
  const observerRef = useRef<IntersectionObserver | null>(null);

  const visibleRef = useRef(new Set<number>());
  /** Pages holding pixels, oldest first -- a Set keeps insertion order. */
  const drawnRef = useRef(new Set<number>());
  const queueRef = useRef<number[]>([]);
  /** The page being painted right now, or 0. Its canvas must not be resized. */
  const busyRef = useRef(0);
  const frameRef = useRef(0);
  const aliveRef = useRef(true);

  const currentPageRef = useRef(currentPage);
  currentPageRef.current = currentPage;

  // A page is only listed once its size is known: the strip has to reserve the
  // right height per page or it jumps about while the document opens.
  const pages = useMemo(() => {
    const numbers: number[] = [];
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      if (pageSizes.has(pageNumber)) numbers.push(pageNumber);
    }
    return numbers;
  }, [pageCount, pageSizes]);

  // --- painting -------------------------------------------------------------

  const release = useCallback((pageNumber: number) => {
    // The busy page is released by the render's own continuation instead:
    // resizing a canvas that pdf.js is drawing into is what makes thumbnails
    // flash (and, mid-render, throw).
    if (busyRef.current === pageNumber) return;
    const canvas = canvasesRef.current.get(pageNumber);
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
    drawnRef.current.delete(pageNumber);
    itemsRef.current.get(pageNumber)?.removeAttribute('data-rendered');
  }, []);

  /** Hand back the thumbnails furthest from the page the user is on. */
  const trim = useCallback(() => {
    if (drawnRef.current.size <= MAX_DRAWN) return;
    const focus = currentPageRef.current;
    const doomed = [...drawnRef.current]
      .filter((pageNumber) => !visibleRef.current.has(pageNumber))
      .sort((a, b) => Math.abs(b - focus) - Math.abs(a - focus) || b - a);
    while (drawnRef.current.size > MAX_DRAWN && doomed.length > 0) {
      release(doomed.shift() as number);
    }
  }, [release]);

  const pumpRef = useRef<() => void>(() => {});

  const pump = useCallback(() => {
    if (busyRef.current || !aliveRef.current) return;
    while (queueRef.current.length > 0) {
      const next = queueRef.current.shift() as number;
      const canvas = canvasesRef.current.get(next);
      // The strip may have scrolled on, or this page may already be painted,
      // between being queued and being reached.
      if (!canvas || drawnRef.current.has(next) || !visibleRef.current.has(next)) continue;

      busyRef.current = next;
      void renderPageTo(next, canvas, THUMB_WIDTH).then((painted) => {
        busyRef.current = 0;
        if (painted && aliveRef.current && visibleRef.current.has(next)) {
          // Re-inserting moves it to the young end of the eviction order.
          drawnRef.current.delete(next);
          drawnRef.current.add(next);
          itemsRef.current.get(next)?.setAttribute('data-rendered', 'true');
          trim();
        } else {
          // Failed, unmounted, or no longer wanted: give the bitmap straight
          // back rather than leaving a page-sized canvas nobody can see.
          release(next);
        }
        pumpRef.current();
      });
      return;
    }
  }, [release, renderPageTo, trim]);
  pumpRef.current = pump;

  /** Coalesces the bursts that scrolling and opening a document both produce. */
  const schedule = useCallback(() => {
    if (frameRef.current) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = 0;
      const focus = currentPageRef.current;
      queueRef.current = [...visibleRef.current]
        .filter(
          (pageNumber) =>
            canvasesRef.current.has(pageNumber) && !drawnRef.current.has(pageNumber),
        )
        .sort((a, b) => Math.abs(a - focus) - Math.abs(b - focus) || a - b);
      trim();
      pump();
    });
  }, [pump, trim]);

  // --- registration ---------------------------------------------------------

  const itemRefs = useMemo(() => {
    const map = new Map<number, (element: HTMLButtonElement | null) => void>();
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      map.set(pageNumber, (element) => {
        const previous = itemsRef.current.get(pageNumber);
        if (previous && previous !== element) observerRef.current?.unobserve(previous);
        if (element) {
          itemsRef.current.set(pageNumber, element);
          observerRef.current?.observe(element);
        } else {
          itemsRef.current.delete(pageNumber);
          visibleRef.current.delete(pageNumber);
        }
      });
    }
    return map;
  }, [pageCount]);

  const canvasRefs = useMemo(() => {
    const map = new Map<number, (element: HTMLCanvasElement | null) => void>();
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      map.set(pageNumber, (element) => {
        if (element) {
          // A fresh canvas is 300 x 150 whether anyone asked for it or not, so
          // empty it: `canvas.width > 0` then means "this thumbnail is drawn".
          element.width = 0;
          element.height = 0;
          canvasesRef.current.set(pageNumber, element);
          schedule();
          return;
        }
        canvasesRef.current.delete(pageNumber);
        drawnRef.current.delete(pageNumber);
      });
    }
    return map;
  }, [pageCount, schedule]);

  // --- observers ------------------------------------------------------------

  useEffect(() => {
    aliveRef.current = true;
    const list = listRef.current;
    if (!list) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const pageNumber = Number((entry.target as HTMLElement).dataset.pageNumber);
          if (!pageNumber) return;
          if (entry.isIntersecting) visibleRef.current.add(pageNumber);
          else visibleRef.current.delete(pageNumber);
        });
        schedule();
      },
      { root: list, rootMargin: `${LOOKAHEAD} 0px`, threshold: 0 },
    );
    observerRef.current = observer;
    // Items register through a ref callback, which runs before this effect on
    // the first mount, so pick up whatever is already there.
    itemsRef.current.forEach((element) => observer.observe(element));
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, [schedule]);

  // A new document: everything painted belongs to the previous one. `pageSizes`
  // is replaced whole when a document opens, so its identity is the signal.
  //
  // A turn is the same problem in miniature: a thumbnail drawn upright is the
  // wrong picture of a page the reader has turned, and the strip would go on
  // showing it because `drawnRef` only knows "painted", not "painted how".
  // `rotationOf` changes identity with the turns, which is that signal.
  useEffect(() => {
    queueRef.current = [];
    [...canvasesRef.current.keys()].forEach((pageNumber) => release(pageNumber));
    schedule();
  }, [pageSizes, rotationOf, release, schedule]);

  useEffect(
    () => () => {
      aliveRef.current = false;
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
      frameRef.current = 0;
      queueRef.current = [];
      busyRef.current = 0;
      [...canvasesRef.current.keys()].forEach((pageNumber) => release(pageNumber));
    },
    [release],
  );

  // The strip follows the document: the page being read is always in view here,
  // otherwise the highlight is a promise the panel does not keep.
  useEffect(() => {
    const list = listRef.current;
    const item = itemsRef.current.get(currentPage);
    if (!list || !item) return;
    const listBox = list.getBoundingClientRect();
    const itemBox = item.getBoundingClientRect();
    if (itemBox.top >= listBox.top && itemBox.bottom <= listBox.bottom) return;
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    list.scrollTo({
      top: item.offsetTop - list.clientHeight / 2 + item.offsetHeight / 2,
      behavior: reduced ? 'auto' : 'smooth',
    });
  }, [currentPage]);

  // --- keyboard -------------------------------------------------------------

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (pages.length === 0) return;
    const first = pages[0];
    const last = pages[pages.length - 1];
    const focused = Number(
      (document.activeElement as HTMLElement | null)?.dataset?.pageNumber ?? currentPage,
    );
    const from = Number.isFinite(focused) && focused > 0 ? focused : currentPage;

    let target: number | null = null;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') target = from + 1;
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') target = from - 1;
    else if (event.key === 'Home') target = first;
    else if (event.key === 'End') target = last;
    if (target === null) return;

    event.preventDefault();
    const clamped = Math.min(Math.max(target, first), last);
    itemsRef.current.get(clamped)?.focus();
  };

  return (
    <aside
      className={`pdf-thumbs${tools ? ' pdf-thumbs--tools' : ''}`}
      data-testid="pdf-thumbs"
      data-pages-busy={tools?.busy ? 'true' : undefined}
      aria-label="Page thumbnails"
    >
      {tools?.error && (
        <p className="pdf-thumbs__error" role="alert" data-testid="pages-error">
          {tools.error}
        </p>
      )}
      <div
        className="pdf-thumbs__list"
        id="pdf-thumbs-list"
        ref={listRef}
        onKeyDown={onKeyDown}
      >
        {pages.map((pageNumber) => {
          // The frame follows the turn, or a landscape thumbnail would be
          // letter-boxed into a portrait box and drawn at the wrong scale.
          const turn = rotationOf(pageNumber);
          const size = pageSizes.get(pageNumber);
          const laid = size
            ? rotatedSize({ width: size.width, height: size.height }, turn)
            : null;
          const height =
            laid && laid.width > 0
              ? Math.round(THUMB_WIDTH * (laid.height / laid.width))
              : Math.round(THUMB_WIDTH * 1.294);
          const isCurrent = pageNumber === currentPage;
          const insert = (after: number) =>
            tools ? (
              <button
                type="button"
                className="pdf-thumbs__insert"
                data-testid={`page-insert-${after}`}
                aria-label={after === 0 ? 'Insert a blank page before page 1' : `Insert a blank page after page ${after}`}
                title="Insert page here (saved with the document)"
                disabled={tools.busy}
                onClick={() => tools.onInsertAfter(after)}
              >
                + Insert page here
              </button>
            ) : null;
          const thumb = (
            <button
              key={pageNumber}
              type="button"
              ref={itemRefs.get(pageNumber)}
              data-page-number={pageNumber}
              data-testid={`pdf-thumb-${pageNumber}`}
              className={`pdf-thumb${isCurrent ? ' pdf-thumb--current' : ''}`}
              aria-label={`Page ${pageNumber}`}
              aria-current={isCurrent ? 'page' : undefined}
              // Roving tab stop: 66 pages must not be 66 stops on the way to
              // the document. Arrow keys move inside the strip.
              tabIndex={isCurrent ? 0 : -1}
              onClick={() => scrollToPage(pageNumber)}
            >
              <span
                className="pdf-thumb__frame"
                style={{ width: THUMB_WIDTH, height }}
                aria-hidden="true"
              >
                <canvas className="pdf-thumb__canvas" ref={canvasRefs.get(pageNumber)} />
              </span>
              <span className="pdf-thumb__label" aria-hidden="true">
                {pageNumber}
              </span>
            </button>
          );
          if (!tools) return thumb;
          const real = tools.rotationOf(pageNumber);
          return (
            <Fragment key={pageNumber}>
              {pageNumber === pages[0] && insert(0)}
              <div
                className={`pdf-thumbs__page${dropAt === pageNumber ? ' is-drop' : ''}${dragFrom === pageNumber ? ' is-dragging' : ''}`}
                data-testid={`page-item-${pageNumber}`}
                data-rotation={real || undefined}
                draggable={!tools.busy}
                onDragStart={(event) => {
                  setDragFrom(pageNumber);
                  event.dataTransfer.effectAllowed = 'move';
                  event.dataTransfer.setData('text/plain', String(pageNumber));
                }}
                onDragOver={(event) => {
                  if (dragFrom === null) return;
                  event.preventDefault();
                  setDropAt(pageNumber);
                }}
                onDragLeave={() => setDropAt((current) => (current === pageNumber ? null : current))}
                onDrop={(event) => {
                  event.preventDefault();
                  const from = dragFrom ?? Number(event.dataTransfer.getData('text/plain'));
                  setDragFrom(null);
                  setDropAt(null);
                  if (!from || from === pageNumber) return;
                  // Dropped on a page: it goes where that page is (after it when moving down).
                  tools.onMove(from, from < pageNumber ? pageNumber - 1 : pageNumber - 1);
                }}
                onDragEnd={() => {
                  setDragFrom(null);
                  setDropAt(null);
                }}
              >
                {thumb}
                <span className="pdf-thumbs__tools" role="group" aria-label={`Page ${pageNumber}`}>
                  <button
                    type="button"
                    className="pdf-thumbs__tool"
                    data-testid={`page-rotate-left-${pageNumber}`}
                    aria-label={`Rotate page ${pageNumber} left (saved with the document)`}
                    title="Rotate left — a real change of the document (the ⟳ above only turns the view)"
                    disabled={tools.busy}
                    onClick={() => tools.onRotate(pageNumber, -90)}
                  >
                    ◀
                  </button>
                  <span className="pdf-thumbs__rotation" data-testid={`page-rotation-${pageNumber}`}>
                    {real ? `${real}°` : ''}
                  </span>
                  <button
                    type="button"
                    className="pdf-thumbs__tool"
                    data-testid={`page-rotate-right-${pageNumber}`}
                    aria-label={`Rotate page ${pageNumber} right (saved with the document)`}
                    title="Rotate right — a real change of the document (the ⟳ above only turns the view)"
                    disabled={tools.busy}
                    onClick={() => tools.onRotate(pageNumber, 90)}
                  >
                    ▶
                  </button>
                  <button
                    type="button"
                    className="pdf-thumbs__tool pdf-thumbs__tool--danger"
                    data-testid={`page-delete-${pageNumber}`}
                    aria-label={`Delete page ${pageNumber}`}
                    title="Delete this page (asks first, and says what is on it)"
                    disabled={tools.busy}
                    onClick={() => tools.onDelete(pageNumber)}
                  >
                    ✕
                  </button>
                </span>
              </div>
              {insert(pageNumber)}
            </Fragment>
          );
        })}
      </div>
    </aside>
  );
}
