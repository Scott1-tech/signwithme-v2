import { useMemo, type CSSProperties, type ReactNode } from 'react';
import type { PdfViewerState } from '../hooks/usePdfViewer';
import { usePdfSearch, type SearchHit } from '../hooks/usePdfSearch';
import { useStoredFlag } from '../hooks/useStoredPreference';
import { pxPointToPt, rotationTransform } from '../lib/viewport';
import type { BBox } from '../types/field';
import { PdfSearchBox } from './PdfSearchBox';
import { PdfSearchMarks } from './PdfSearchMarks';
import { PdfThumbnails, type PageTools } from './PdfThumbnails';

/** Box a free (non-slot) placement gets, in PDF points, centred on the click. */
const PLACEMENT_SIZE = { width: 180, height: 34 };

/**
 * Below this the review screen stacks (see the `1024px` media query in
 * `index.css`) and the document is already short of width, so the page strip
 * starts folded away -- until the reader says otherwise, which is remembered.
 */
const NARROW_SCREEN_PX = 1024;

/**
 * What the reader is told while a page is turned. A turned page is for
 * *reading*: the marks are still drawn in the right place (the overlay is
 * turned by the same map as the page), but nothing on them may be moved,
 * because the hooks that move them -- `useSlotDrag`, and the click-to-place
 * below -- convert pointer travel straight through the render scale and know
 * nothing about a turn. Silently letting a drag through would put a signature
 * somewhere the reader did not aim at.
 */
const ROTATED_PLACEMENT_NOTE =
  'Rotated view — placement disabled; rotate back to place signatures';
const ROTATED_READONLY_NOTE = 'Rotated view — marks are read-only; rotate back to interact';

function widescreen(): boolean {
  return typeof window === 'undefined' ? true : window.innerWidth > NARROW_SCREEN_PX;
}

interface Props {
  viewer: PdfViewerState;
  /**
   * One page's overlay content, drawn inside that page's own positioning
   * context. Called once per page with the page number, so a caller never has
   * to know which page is "current" -- every page renders its own marks and
   * `lib/viewport.ts` keeps the maths page-local.
   *
   * Whatever is returned becomes a **direct child** of `.pdf-overlay` (return a
   * fragment, not a wrapper element): the overlay's `pointer-events` rules are
   * written against its immediate children.
   */
  renderPageOverlay?: (pageNumber: number) => ReactNode;
  /**
   * One page's text layer (plan §3, WE), drawn BETWEEN the search marks and
   * the overlay. Only called for the pages of the render window
   * (`viewer.windowPages`) that the reader has not turned; pass `undefined`
   * while no tool needs the page's text, and no page carries a layer.
   */
  renderTextLayer?: (pageNumber: number) => ReactNode;
  /** Click-to-place mode. Receives PDF-point coordinates, top-left origin --
   *  the same space the API and the PDF builder use. */
  interactive?: boolean;
  onCanvasClick?: (page: number, point: { x: number; y: number }, suggested: BBox) => void;
  /**
   * «Highlight all matches» (W3): the search's matches, handed out as they
   * are (unrotated PDF points). Without it the search box has no such button.
   */
  onHighlightMatches?: (hits: SearchHit[]) => void;
  /**
   * Floats OVER the top of the page area (`.pdf-body__float`), out of the
   * layout: whatever appears or goes here never moves a page. The workspace's
   * passing notes live here (fix 2026-09-27) -- an in-flow note that vanished
   * as a drag began slid the page 74 px under the pointer.
   */
  floating?: ReactNode;
  /** F2: the page tools on the strip (insert / delete / move / rotate), or none. */
  pageTools?: PageTools | null;
}

/**
 * Continuous scroll: every page of the document is laid out in one column and
 * the user reaches page 51 by scrolling, not by pressing "Next" fifty times.
 * Only the pages near the viewport hold pixels -- `usePdfViewer` owns that
 * bookkeeping; this component only supplies the boxes and the canvases.
 *
 * Each page is three stacked layers inside one `.pdf-page` box, in paint order:
 * the canvas, the search marks, then the overlay the caller fills. The order
 * is the contract -- a highlight must never cover a slot box.
 */
export function PdfViewer({
  viewer,
  renderPageOverlay,
  renderTextLayer,
  interactive = false,
  onCanvasClick,
  onHighlightMatches,
  floating,
  pageTools = null,
}: Props) {
  const {
    containerRef,
    registerPage,
    registerCanvas,
    scale,
    currentPage,
    pageCount,
    pageSizes,
    sizeOf,
    rotationOf,
    viewRotation,
    anyRotated,
    windowPages,
    loading,
    error,
  } = viewer;

  // A preference, not state the document depends on: folding the strip away
  // only changes how much width the pages get (the hook re-fits itself).
  const [thumbsOpen, setThumbsOpen] = useStoredFlag('swm.thumbs', widescreen());

  const search = usePdfSearch(viewer);

  const pages = useMemo(() => {
    const numbers: number[] = [];
    // A page is only laid out once its size is known: a placeholder of the
    // wrong height would make the scrollbar lie and every later page jump.
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      if (pageSizes.has(pageNumber)) numbers.push(pageNumber);
    }
    return numbers;
  }, [pageCount, pageSizes]);

  // Ref callbacks must keep their identity across renders: an inline arrow
  // would detach and re-attach every page and canvas on each render, which
  // cancels renders in flight and re-observes the whole document.
  const pageRefs = useMemo(() => {
    const map = new Map<number, (element: HTMLDivElement | null) => void>();
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      map.set(pageNumber, (element) => registerPage(pageNumber, element));
    }
    return map;
  }, [pageCount, registerPage]);

  const canvasRefs = useMemo(() => {
    const map = new Map<number, (element: HTMLCanvasElement | null) => void>();
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      map.set(pageNumber, (element) => registerCanvas(pageNumber, element));
    }
    return map;
  }, [pageCount, registerCanvas]);

  const handleClick =
    (pageNumber: number, turned: boolean) => (event: React.MouseEvent<HTMLDivElement>) => {
      if (!interactive || !onCanvasClick) return;
      // A turned page never places: see `ROTATED_PLACEMENT_NOTE`.
      if (turned) return;
      const rect = event.currentTarget.getBoundingClientRect();
      // CSS pixels back to PDF points, relative to this page's own top-left
      // corner (`lib/viewport.ts` holds the one inverse of the render scale).
      const { x, y } = pxPointToPt(
        { x: event.clientX - rect.left, y: event.clientY - rect.top },
        scale,
      );
      onCanvasClick(pageNumber, { x, y }, {
        x: Math.max(x - PLACEMENT_SIZE.width / 2, 0),
        y: Math.max(y - PLACEMENT_SIZE.height / 2, 0),
        width: PLACEMENT_SIZE.width,
        height: PLACEMENT_SIZE.height,
      });
    };

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <button
          type="button"
          className={`pdf-toolbar__thumbs${thumbsOpen ? ' is-open' : ''}`}
          data-testid="pdf-thumbs-toggle"
          aria-expanded={thumbsOpen}
          aria-controls="pdf-thumbs-list"
          aria-label={thumbsOpen ? 'Hide page thumbnails' : 'Show page thumbnails'}
          onClick={() => setThumbsOpen(!thumbsOpen)}
        >
          <span aria-hidden="true">▤</span> Pages
        </button>
        <button type="button" onClick={() => viewer.scrollToPage(currentPage - 1)}
                disabled={currentPage <= 1}>
          ‹ Prev
        </button>
        <span className="pdf-toolbar__page">
          Page {currentPage} of {pageCount || '–'}
        </span>
        <button type="button"
                onClick={() => viewer.scrollToPage(currentPage + 1)}
                disabled={currentPage >= pageCount}>
          Next ›
        </button>

        <PdfSearchBox search={search} onHighlightAll={onHighlightMatches} />

        <span className="pdf-toolbar__spacer" />
        <button type="button" onClick={viewer.zoomOut}>−</button>
        <span className="pdf-toolbar__zoom">{Math.round(scale * 100)}%</span>
        <button type="button" onClick={viewer.zoomIn}>+</button>
        <button type="button" onClick={() => viewer.fitToWidth()}>
          Fit
        </button>
        {/* Turns the page the toolbar is counting, not the document: a scan
            with one sheet fed in sideways is the case this exists for. Four
            presses come back to where it started. */}
        <button
          type="button"
          className={`pdf-toolbar__rotate${viewRotation !== 0 ? ' is-rotated' : ''}`}
          data-testid="pdf-rotate"
          aria-label="Rotate page clockwise"
          title={`Rotate page ${currentPage} 90° clockwise (view only)`}
          onClick={() => viewer.rotatePage(currentPage)}
          disabled={pageCount === 0}
        >
          <span aria-hidden="true">⟳</span>
        </button>
        {viewRotation !== 0 && (
          <span className="pdf-toolbar__rotation" data-testid="pdf-rotation-value">
            {viewRotation}°
          </span>
        )}
      </div>

      {anyRotated && (
        <p className="pdf-rotation-note" role="status" data-testid="pdf-rotation-note">
          {interactive ? ROTATED_PLACEMENT_NOTE : ROTATED_READONLY_NOTE}
        </p>
      )}

      {/* The strip and the document scroll independently, side by side. The
          stage keeps its slot in this list whether the strip is mounted or
          not, so toggling the strip never remounts the scroll container --
          which would drop the scroll position and every observer bound to it. */}
      <div className="pdf-body">
        {floating ? <div className="pdf-body__float">{floating}</div> : null}
        {thumbsOpen && <PdfThumbnails viewer={viewer} tools={pageTools} />}

        <div className="pdf-stage" ref={containerRef}>
          {loading && <p className="pdf-status">Loading document…</p>}
          {error && <p className="pdf-status pdf-status--error">{error}</p>}
          <div className="pdf-pages">
            {pages.map((pageNumber) => {
              // `sizeOf` is the box as laid out (turned); the layers inside
              // are still the unturned page, and the transform below is what
              // brings them onto it -- one map for the search marks and the
              // overlay both, so they cannot drift apart.
              const size = sizeOf(pageNumber);
              const raw = pageSizes.get(pageNumber);
              const flat = {
                width: (raw?.width ?? 0) * scale,
                height: (raw?.height ?? 0) * scale,
              };
              const turn = rotationOf(pageNumber);
              const turned = turn !== 0;
              const layerStyle: CSSProperties | undefined = turned
                ? {
                    left: 0,
                    top: 0,
                    right: 'auto',
                    bottom: 'auto',
                    width: flat.width,
                    height: flat.height,
                    transform: rotationTransform(turn, flat.width, flat.height),
                    transformOrigin: '0 0',
                  }
                : undefined;
              const pageHits = search.hitsOnPage(pageNumber);

              return (
                <div
                  key={pageNumber}
                  ref={pageRefs.get(pageNumber)}
                  data-page-number={pageNumber}
                  data-rotation={turned ? turn : undefined}
                  className={
                    `pdf-page${interactive && !turned ? ' pdf-page--interactive' : ''}` +
                    `${pageNumber === currentPage ? ' pdf-page--current' : ''}` +
                    `${turned ? ' pdf-page--rotated' : ''}`
                  }
                  style={{ width: size.width || undefined, height: size.height || undefined }}
                  onClick={handleClick(pageNumber, turned)}
                >
                  {/* Shown until the canvas has this page; hidden by
                      `[data-rendered]`, which the hook sets when it lands. */}
                  <span className="pdf-page__placeholder" aria-hidden="true">
                    {pageNumber}
                  </span>
                  <canvas ref={canvasRefs.get(pageNumber)} className="pdf-canvas" />
                  <PdfSearchMarks
                    hits={pageHits}
                    activeIndex={search.activeIndex}
                    scale={scale}
                    rotation={turn}
                    size={flat}
                  />
                  {/* The page's own text (WE): under the overlay, over the
                      search marks; never on a turned page, only in the window. */}
                  {renderTextLayer && !turned && windowPages.has(pageNumber)
                    ? renderTextLayer(pageNumber)
                    : null}
                  <div className="pdf-overlay" style={layerStyle}>
                    {renderPageOverlay?.(pageNumber)}
                    {/* A turned page's marks are correct but inert. This pane
                        is what makes that true whatever the caller put in the
                        overlay: it is the last child and sits above every one
                        of them, so no drag, no slot click and no stray
                        `pointer-events: auto` rule can reach through it. */}
                    {turned && (
                      <span
                        className="pdf-overlay__shield"
                        data-testid={`pdf-rotation-shield-${pageNumber}`}
                        aria-hidden="true"
                        onClick={(event) => event.stopPropagation()}
                      />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
