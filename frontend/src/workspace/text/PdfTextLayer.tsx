import { memo, useEffect, useRef, useState, type CSSProperties } from 'react';
import type { TextLayerHandle, TextLayerTask } from '../../hooks/usePdfViewer';
import { mergeRunsIntoLines, type TextRun } from '../../lib/textRuns';

/** How the armed tool uses the page's text. */
export type TextLayerMode = 'lines' | 'select';

interface Props {
  pageNumber: number;
  /** CSS pixels per PDF point (the viewer's render scale). */
  scale: number;
  /** `usePdfViewer().renderTextLayer` -- stable. */
  renderTextLayer: (pageNumber: number, container: HTMLDivElement) => TextLayerTask;
  /**
   * `lines` (edit-text, WE): the merged lines are buttons, a click hands one to
   * `onLine`. `select` (highlight / underline / strikeout, W3): no line
   * buttons -- the spans themselves take the pointer and the user SELECTS
   * text (`useTextSelection` reads it).
   */
  mode?: TextLayerMode;
  /** A line was clicked (the armed `text-run` tool decides what that means). */
  onLine?: (pageNumber: number, line: TextRun) => void;
  /**
   * The drawn layer (or `null` once it goes): the text-selection hook needs
   * the spans' container and the runs to turn a selection into boxes.
   */
  onReady?: (pageNumber: number, layer: { container: HTMLDivElement; handle: TextLayerHandle } | null) => void;
}

/**
 * One page's own text, laid over the canvas (plan §2.4 layer 3, §3), between
 * the search marks and the overlay.
 *
 * Two parts:
 *
 *  * `.textLayer` -- pdf.js 4.8.69's `TextLayer`: one transparent `<span>`
 *    per text item, positioned in % of the page and sized in
 *    `var(--scale-factor)` (set here from `scale`), so a zoom only moves one
 *    CSS variable. In `select` mode (W3) it takes the pointer
 *    (`workspace--text-select`) and the browser's own selection runs over it.
 *  * `.pdf-text-layer__lines` (`lines` mode only) -- the items merged into
 *    LINES (`lib/textRuns.ts mergeRunsIntoLines`): one transparent `<button
 *    class="pdf-text-line">` per line over its ink box. Hover frames it (the
 *    Sejda way), a click hands it to the armed tool (`onLine`).
 *
 * Mounted by `Workspace` only while a tool that needs it is armed, only on the
 * pages of the render window (`windowPages`, ≤ 6), and never on a page the
 * reader has turned -- so a 66-page contract never carries more than six
 * pages of spans. Unmounting cancels the pdf.js task and empties the DOM.
 */
export const PdfTextLayer = memo(function PdfTextLayer({
  pageNumber,
  scale,
  renderTextLayer,
  mode = 'lines',
  onLine,
  onReady,
}: Props) {
  const spansRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<TextRun[] | null>(null);
  const readyRef = useRef(onReady);
  readyRef.current = onReady;

  useEffect(() => {
    const container = spansRef.current;
    if (!container) return undefined;
    let live = true;
    setLines(null);
    const task = renderTextLayer(pageNumber, container);
    void task.done.then((handle) => {
      if (!live) return;
      setLines(handle ? mergeRunsIntoLines(handle.runs) : []);
      if (handle) readyRef.current?.(pageNumber, { container, handle });
    });
    return () => {
      live = false;
      task.cancel();
      readyRef.current?.(pageNumber, null);
    };
  }, [pageNumber, renderTextLayer]);

  const px = (value: number) => value * scale;

  return (
    <div
      className={`pdf-text-layer pdf-text-layer--${mode}`}
      data-testid={`pdf-text-layer-${pageNumber}`}
      data-page-number={pageNumber}
      data-mode={mode}
      data-state={lines ? 'ready' : 'loading'}
      data-line-count={lines ? lines.length : undefined}
      style={{ '--scale-factor': String(scale) } as CSSProperties}
    >
      <div className="textLayer pdf-text-layer__spans" ref={spansRef} data-page-number={pageNumber} />
      {lines && mode === 'lines' && (
        <div className="pdf-text-layer__lines">
          {lines.map((line) => (
            <button
              key={line.index}
              type="button"
              tabIndex={-1}
              className="pdf-text-line"
              data-testid="pdf-text-line"
              data-line-index={line.index}
              data-text={line.text}
              data-x-pt={line.x.toFixed(2)}
              data-y-pt={line.y.toFixed(2)}
              data-w-pt={line.width.toFixed(2)}
              data-h-pt={line.height.toFixed(2)}
              data-baseline-pt={line.baseline.toFixed(2)}
              data-font-size={line.fontSize.toFixed(2)}
              data-font-family={line.fontFamily}
              data-bold={line.bold ? 'true' : 'false'}
              data-italic={line.italic ? 'true' : 'false'}
              data-runs={line.runs?.length ?? 1}
              aria-label={`Edit line: ${line.text}`}
              title="Click to edit this line"
              style={{
                left: px(line.x),
                top: px(line.y),
                width: Math.max(2, px(line.width)),
                height: Math.max(2, px(line.height)),
              }}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                onLine?.(pageNumber, line);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
});
