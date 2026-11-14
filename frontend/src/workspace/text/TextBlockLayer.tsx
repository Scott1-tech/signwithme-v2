import { memo, type CSSProperties, type ReactNode } from 'react';
import type { FitMode } from '../../api/document';
import { editorFontCss } from '../../lib/editorFonts';
import { clientToLayerPt, frameLayerStyle, UNROTATED, type PageFrame } from '../../lib/viewport';
import { lineTopFor, type BoxPt, type EditResult } from './textOps';
import { reasonText, type EditableBlock, type PageTextModel } from './useServerTextModel';

/**
 * F1b: one page's editable text, as the SERVER sees it (EDITOR-V2-PLAN §7).
 *
 * One frame per block of the server's text model (`useServerTextModel`):
 * hover frames it (the Sejda way), a click opens the editor on it. A block
 * the server cannot edit (a scanned page, a Type 3 font, RTL, vertical
 * text) says so -- `not-allowed` cursor and the reason as its title -- and
 * does nothing on click.
 *
 * Also drawn here, in page coordinates:
 *  * `pending` -- an edit the server's preview does not show yet: the new
 *    text in the server's face, over a mask of the paper's own colour (the
 *    median of the canvas under it -- never a fixed white). It goes as soon
 *    as the page's preview arrives; nothing of it is ever exported;
 *  * `overflow` -- an edit whose text does not fit: a red dashed zone as tall
 *    as the text needs, and the three ways out (Shrink to fit / Allow
 *    overlap / Keep editing);
 *  * `children` -- the open editor (and its toolbar), when it is on this page.
 *
 * ROTFIX: the blocks are the engine's (UNROTATED page); on a page with its own
 * `/Rotate` the whole layer is turned onto the box (`frameLayerStyle`) and a
 * click is turned back (`clientToLayerPt`).
 */

export interface PendingEdit {
  block: EditableBlock;
  edit: EditResult;
  mask: string | null;
}

export interface OverflowNotice {
  opId: string;
  block: EditableBlock;
  box: BoxPt;
  linesOver: number;
  neededH: number | null;
  collision: boolean;
}

interface Props {
  pageNumber: number;
  scale: number;
  /** ROTFIX: the page's own `/Rotate` + unrotated size. */
  frame?: PageFrame | null;
  model: PageTextModel | null;
  error: string | null;
  /** Blocks with an op in the draft (framed as edited). */
  editedBlocks: ReadonlySet<string>;
  openBlockId: string | null;
  pending: readonly PendingEdit[];
  overflow: readonly OverflowNotice[];
  /** The editor's own mask (until the page's preview without the block is painted). */
  openMask: { box: BoxPt; color: string } | null;
  onOpen: (block: EditableBlock, point: { x: number; y: number }, clicks: number) => void;
  onFit: (opId: string, fit: FitMode) => void;
  onKeepEditing: (block: EditableBlock) => void;
  children?: ReactNode;
}

/** A run of at least this many '_' in a row is a blank to fill in, not text to edit. */
const BLANK_MIN_CHARS = 3;

/**
 * The clickable part of a block, as a CSS `clip-path`: everything except its
 * fill-in blanks (runs of '_'). A click on a blank falls through to the page,
 * where the tool adds NEW text on the line (the Sejda way) -- the words next to
 * the blank are not touched and do not move. `undefined`: no blanks, the whole
 * box is clickable. A block that is only a blank is not clickable at all.
 */
function clickableClip(block: EditableBlock, scale: number): string | undefined {
  const blanks: Array<[number, number]> = [];
  let start: number | null = null;
  let end = 0;
  let count = 0;
  const close = () => {
    if (start !== null && count >= BLANK_MIN_CHARS) blanks.push([start, end]);
    start = null;
    count = 0;
  };
  block.chars.forEach((char) => {
    if (char.c === '_') {
      if (start === null) start = char.x0;
      end = char.x1;
      count += 1;
    } else {
      close();
    }
  });
  close();
  if (blanks.length === 0) return undefined;

  const left = block.bbox.x;
  const right = block.bbox.x + block.bbox.w;
  const height = Math.max(2, block.bbox.h * scale) + 4;
  const parts: string[] = [];
  let cursor = left;
  const add = (from: number, to: number) => {
    if (to - from < 0.5) return;
    const a = ((from - left) * scale).toFixed(1);
    const b = ((to - left) * scale + 2).toFixed(1);
    parts.push(`M${a} -2H${b}V${height.toFixed(1)}H${a}Z`);
  };
  blanks.forEach(([from, to]) => {
    add(cursor, Math.min(from, right));
    cursor = Math.max(cursor, to);
  });
  add(cursor, right);
  return `path('${parts.length > 0 ? parts.join(' ') : 'M0 0Z'}')`;
}

export const TextBlockLayer = memo(function TextBlockLayer({
  pageNumber,
  scale,
  frame = null,
  model,
  error,
  editedBlocks,
  openBlockId,
  pending,
  overflow,
  openMask,
  onOpen,
  onFit,
  onKeepEditing,
  children,
}: Props) {
  const px = (value: number) => value * scale;
  const boxStyle = (box: BoxPt): CSSProperties => ({
    left: px(box.x),
    top: px(box.y),
    width: Math.max(2, px(box.w)),
    height: Math.max(2, px(box.h)),
  });

  return (
    <div
      className="tb-layer"
      data-testid={`text-block-layer-${pageNumber}`}
      data-page-number={pageNumber}
      data-state={model ? 'ready' : error ? 'error' : 'loading'}
      data-block-count={model ? model.blocks.length : undefined}
      data-space="unrotated"
      title={error ?? undefined}
      style={frameLayerStyle(frame, UNROTATED, scale)}
    >
      {pending.map(({ block, edit, mask }) => {
        const box = edit.box ?? block.bbox;
        const style = { ...block.style, ...(edit.override ?? {}) };
        const lineHeight = block.rowGap ?? 1.2 * style.size;
        const top = lineTopFor(block.firstBaseline + (box.y - block.bbox.y), style.family, style.size, lineHeight);
        return (
          <div key={`pending-${block.id}`} className="tb-pending" data-testid="text-block-pending" data-block-id={block.id}>
            <div
              className="tb-mask"
              style={{ ...boxStyle(block.bbox), background: mask ?? 'transparent' }}
              aria-hidden="true"
            />
            {edit.text.trim() !== '' && (
              <div
                className="tb-pending__text"
                style={{
                  left: px(box.x),
                  top: px(top),
                  minWidth: px(box.w),
                  fontFamily: editorFontCss(style.family),
                  fontWeight: style.bold ? 700 : 400,
                  fontStyle: style.italic ? 'italic' : 'normal',
                  fontSize: px(style.size),
                  lineHeight: `${px(lineHeight)}px`,
                  color: style.color,
                }}
              >
                {edit.text}
              </div>
            )}
          </div>
        );
      })}

      {openMask && (
        <div
          className="tb-mask tb-mask--open"
          data-testid="text-block-mask"
          style={{ ...boxStyle(openMask.box), background: openMask.color }}
          aria-hidden="true"
        />
      )}

      {model?.blocks.map((block) => {
        const open = block.id === openBlockId;
        const edited = editedBlocks.has(block.id);
        return (
          <button
            key={block.id}
            type="button"
            tabIndex={-1}
            className={`tb-block${block.editable ? '' : ' tb-block--locked'}${edited ? ' tb-block--edited' : ''}${
              open ? ' tb-block--open' : ''
            }`}
            data-testid="text-block"
            data-block-id={block.id}
            data-page-uid={block.pageUid}
            data-text={block.text}
            data-editable={block.editable ? 'true' : 'false'}
            data-edited={edited ? 'true' : undefined}
            data-x-pt={block.bbox.x.toFixed(2)}
            data-y-pt={block.bbox.y.toFixed(2)}
            data-w-pt={block.bbox.w.toFixed(2)}
            data-h-pt={block.bbox.h.toFixed(2)}
            data-baseline-pt={block.firstBaseline.toFixed(2)}
            data-family={block.style.family}
            data-size={block.style.size}
            aria-label={block.editable ? `Edit text: ${block.text}` : `Cannot edit: ${reasonText(block.reason)}`}
            aria-disabled={!block.editable}
            title={block.editable ? 'Click to edit this text' : reasonText(block.reason)}
            style={{ ...boxStyle(block.bbox), clipPath: clickableClip(block, scale) }}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              if (!block.editable || open) return;
              const layer = event.currentTarget.parentElement as HTMLElement;
              onOpen(block, clientToLayerPt(layer, event.clientX, event.clientY, frame, UNROTATED, scale), event.detail);
            }}
          />
        );
      })}

      {overflow.map((notice) => {
        const zoneHeight = Math.max(notice.box.h, notice.neededH ?? notice.box.h + notice.linesOver * 1.2 * notice.block.style.size);
        return (
          <div key={`overflow-${notice.opId}`} className="tb-overflow" data-testid="text-overflow" data-op-id={notice.opId}>
            <div className="tb-overflow__zone" style={{ ...boxStyle({ ...notice.box, h: zoneHeight }) }} aria-hidden="true" />
            <div
              className="tb-overflow__panel"
              role="alertdialog"
              aria-label="Text does not fit"
              style={{ left: px(notice.box.x), top: px(notice.box.y + zoneHeight) + 4 }}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
            >
              <p className="tb-overflow__text" data-testid="text-overflow-message">
                {notice.collision
                  ? 'The new text would run into other text on the page.'
                  : `Text overflows by ${notice.linesOver} line${notice.linesOver === 1 ? '' : 's'}.`}{' '}
                Neighbouring text is never moved.
              </p>
              <div className="tb-overflow__actions">
                <button type="button" className="sj-btn" data-testid="text-overflow-shrink" onClick={() => onFit(notice.opId, 'shrink')}>
                  Shrink to fit
                </button>
                <button type="button" className="sj-btn" data-testid="text-overflow-overlap" onClick={() => onFit(notice.opId, 'overflow')}>
                  Allow overlap
                </button>
                <button type="button" className="sj-btn sj-btn--primary" data-testid="text-overflow-keep" onClick={() => onKeepEditing(notice.block)}>
                  Keep editing
                </button>
              </div>
            </div>
          </div>
        );
      })}

      {children}
    </div>
  );
});
