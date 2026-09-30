import { useEffect, useLayoutEffect, useRef, type CSSProperties, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { editorFontCss } from '../../lib/editorFonts';
import { unrotateVector, type Quarter } from '../../lib/viewport';
import { lineTopFor, type BoxPt } from './textOps';
import type { EditableBlock, TextStyle } from './useServerTextModel';

/**
 * F1b: the editor on one block of the PDF's own text (EDITOR-V2-PLAN §7).
 *
 * A plain-text `contenteditable` laid exactly over the block: same left
 * edge, first baseline on the PDF's first baseline, set in the face the
 * SERVER will print it in (the Liberation substitute of the span's font, via
 * `@font-face` -- `lib/fonts.ts`), so what is typed is where and how it will
 * be printed. What it is NOT: a box painted over the page. Under it the page
 * is the server's preview WITHOUT this block (`…/preview?hide=<block>`), and
 * until that preview is painted a mask of the paper's own colour stands in.
 *
 * Keys: Enter = done (Shift+Enter = a new line), Esc = cancel, Ctrl+B /
 * Ctrl+I = bold / italic of the block, Backspace / Delete / Shift+arrows /
 * Ctrl+arrows = the browser's own. Paste is plain text, NFC. Nothing typed
 * here reaches the workspace's one-letter shortcuts.
 */

interface Props {
  block: EditableBlock;
  scale: number;
  /** ROTFIX: the page's own `/Rotate` -- the editor lives in the (turned) unrotated layer. */
  turn?: Quarter;
  /** The text the editor starts with (the block's own, or its op's). */
  initialText: string;
  /** Selected when it opens ([start, end) in `initialText`). */
  initialSelection: [number, number];
  style: TextStyle;
  box: BoxPt;
  /** The block may be moved (the toolbar's ✥ Move). */
  moving: boolean;
  pageWidth: number;
  pageHeight: number;
  onText: (text: string) => void;
  onCommit: (text: string) => void;
  onCancel: () => void;
  onToggle: (which: 'bold' | 'italic') => void;
  /** A new box (moved / widened by hand). */
  onBox: (box: BoxPt) => void;
  /** The owner reads the text / gives the focus back through this (toolbar, outside click). */
  handleRef?: React.MutableRefObject<EditorHandle | null>;
}

/** The editor's text: text nodes as they are, a `<br>` or a block element = a new line. */
export function readEditable(root: HTMLElement | null): string {
  if (!root) return '';
  let out = '';
  const walk = (node: Node) => {
    node.childNodes.forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) out += (child as Text).data;
      else if (child.nodeName === 'BR') out += '\n';
      else {
        const block = /^(DIV|P)$/.test(child.nodeName);
        if (block && out && !out.endsWith('\n')) out += '\n';
        walk(child);
      }
    });
  };
  walk(root);
  return out.replace(/\r\n?/g, '\n');
}

export interface EditorHandle {
  read(): string;
  focus(): void;
  element(): HTMLDivElement | null;
}

export function TextBlockEditor({
  block,
  scale,
  turn = 0,
  initialText,
  initialSelection,
  style,
  box,
  moving,
  pageWidth,
  pageHeight,
  onText,
  onCommit,
  onCancel,
  onToggle,
  onBox,
  handleRef,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  if (handleRef) {
    handleRef.current = {
      read: () => readEditable(ref.current),
      focus: () => ref.current?.focus({ preventScroll: true }),
      element: () => ref.current,
    };
  }
  const px = (value: number) => value * scale;
  const lineHeight = block.rowGap ?? 1.2 * style.size;
  const baseline = block.firstBaseline + (box.y - block.bbox.y);
  const top = lineTopFor(baseline, style.family, style.size, lineHeight);

  // The text is set ONCE: re-rendering it from props would throw the caret away.
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.textContent = initialText;
    element.focus({ preventScroll: true });
    const node = element.firstChild;
    const selection = window.getSelection();
    if (!selection) return;
    const range = document.createRange();
    if (node && node.nodeType === Node.TEXT_NODE) {
      const length = (node.textContent ?? '').length;
      range.setStart(node, Math.min(initialSelection[0], length));
      range.setEnd(node, Math.min(initialSelection[1], length));
    } else {
      range.selectNodeContents(element);
      range.collapse(false);
    }
    selection.removeAllRanges();
    selection.addRange(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on open only
  }, []);

  const read = () => readEditable(ref.current);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // Nothing typed here is a workspace shortcut.
    event.stopPropagation();
    const mod = event.ctrlKey || event.metaKey;
    if (event.key === 'Enter' && !event.shiftKey && !mod) {
      event.preventDefault();
      onCommit(read());
      return;
    }
    if (event.key === 'Enter' && event.shiftKey) {
      event.preventDefault();
      document.execCommand('insertText', false, '\n');
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
      return;
    }
    if (mod && (event.key === 'b' || event.key === 'B')) {
      event.preventDefault();
      onToggle('bold');
      return;
    }
    if (mod && (event.key === 'i' || event.key === 'I')) {
      event.preventDefault();
      onToggle('italic');
    }
  };

  // Keep the pointer's own work (selection by drag, 2x word, 3x line) inside.
  const stop = (event: ReactPointerEvent) => event.stopPropagation();

  /** Drag one handle: the right edge (width) or the ✥ (position). */
  const drag = (kind: 'width' | 'move') => (event: ReactPointerEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startY = event.clientY;
    const start = { ...box };
    const onMove = (move: PointerEvent) => {
      const { x: dx, y: dy } = unrotateVector((move.clientX - startX) / scale, (move.clientY - startY) / scale, turn);
      if (kind === 'width') {
        const w = Math.min(Math.max(8, start.w + dx), pageWidth - start.x);
        onBox({ ...start, w });
      } else {
        const x = Math.min(Math.max(0, start.x + dx), Math.max(0, pageWidth - start.w));
        const y = Math.min(Math.max(0, start.y + dy), Math.max(0, pageHeight - start.h));
        onBox({ ...start, x, y });
      }
    };
    const onUp = () => {
      target.removeEventListener('pointermove', onMove);
      target.removeEventListener('pointerup', onUp);
      target.removeEventListener('pointercancel', onUp);
      ref.current?.focus({ preventScroll: true });
    };
    target.addEventListener('pointermove', onMove);
    target.addEventListener('pointerup', onUp);
    target.addEventListener('pointercancel', onUp);
  };

  useEffect(() => {
    onText(read());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, the starting text
  }, []);

  const frame: CSSProperties = {
    left: px(box.x),
    top: px(top),
    width: px(box.w),
    minHeight: px(Math.max(lineHeight, box.h)),
  };

  return (
    <div className="tb-editor-frame" style={frame} data-testid="text-block-editor-frame">
      <div
        ref={ref}
        className="tb-editor"
        data-testid="text-block-editor"
        data-block-id={block.id}
        data-family={style.family}
        data-size={style.size}
        data-bold={style.bold ? 'true' : 'false'}
        data-italic={style.italic ? 'true' : 'false'}
        data-color={style.color}
        data-x-pt={box.x.toFixed(2)}
        data-baseline-pt={baseline.toFixed(2)}
        data-w-pt={box.w.toFixed(2)}
        role="textbox"
        aria-multiline="true"
        aria-label="Edit the document’s text"
        contentEditable="plaintext-only"
        suppressContentEditableWarning
        spellCheck={false}
        style={{
          fontFamily: editorFontCss(style.family),
          fontWeight: style.bold ? 700 : 400,
          fontStyle: style.italic ? 'italic' : 'normal',
          fontSize: px(style.size),
          lineHeight: `${px(lineHeight)}px`,
          color: style.color,
          fontKerning: 'none',
        }}
        onKeyDown={onKeyDown}
        onInput={() => onText(read())}
        onPaste={(event) => {
          event.preventDefault();
          const text = event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n').normalize('NFC');
          if (text) document.execCommand('insertText', false, text);
        }}
        onDrop={(event) => event.preventDefault()}
        onPointerDown={stop}
        onClick={(event) => event.stopPropagation()}
      />
      <span
        className="tb-editor__width"
        data-testid="text-block-width"
        role="separator"
        aria-label="Drag to change the width of the text box"
        title="Drag to change the width"
        onPointerDown={drag('width')}
      />
      {moving && (
        <span
          className="tb-editor__move"
          data-testid="text-block-move-handle"
          aria-label="Drag to move the text"
          title="Drag to move the text"
          onPointerDown={drag('move')}
        >
          ✥
        </span>
      )}
    </div>
  );
}
