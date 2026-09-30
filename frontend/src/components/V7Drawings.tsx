import { useLayoutEffect, useRef, type CSSProperties } from 'react';
import {
  ANNOTATION_COLOR_HEX,
  type StampAnnotation,
  type TextboxAnnotation,
} from '../lib/annotations';
import { editorFontCss, EDITOR_FONT_LABEL } from '../lib/editorFonts';
import { layoutStamp, layoutTextbox } from '../lib/annotationKinds/v7';
import { ptToPx } from '../lib/viewport';

/**
 * F2: the two v7 boxes drawn the way the engine prints them
 * (`app/services/pdf_engine/markup.py`): a text box laid out by the same
 * greedy word wrap on the same baselines (`layoutTextbox`), a stamp with the
 * same border, label size and date line (`layoutStamp`). Rotation turns the
 * box about its own centre, clockwise as displayed; opacity is the whole
 * element's.
 */

function boxTransform(item: { x_pt: number; y_pt: number; w_pt: number; h_pt: number; rotation_deg?: number }, scale: number) {
  const x = ptToPx(item.x_pt, scale);
  const y = ptToPx(item.y_pt, scale);
  const rotation = item.rotation_deg ?? 0;
  if (!rotation) return `translate(${x} ${y})`;
  const cx = ptToPx(item.w_pt, scale) / 2;
  const cy = ptToPx(item.h_pt, scale) / 2;
  return `translate(${x} ${y}) rotate(${rotation} ${cx} ${cy})`;
}

export function renderTextbox(item: TextboxAnnotation, scale: number, hidden = false) {
  const lines = layoutTextbox(item);
  const width = item.w_pt;
  return (
    <g
      key={item.id}
      className="swm-ann__textbox"
      data-testid={`ann-textbox-${item.id}`}
      data-ann-id={item.id}
      data-line-count={lines.length}
      data-align={item.align}
      data-line-height={item.line_height}
      data-font={item.runs[0]?.font}
      transform={boxTransform(item, scale)}
      opacity={item.opacity ?? 1}
      visibility={hidden ? 'hidden' : undefined}
    >
      {lines.map((line, index) => {
        const offset =
          item.align === 'center' ? (width - line.width) / 2 : item.align === 'right' ? width - line.width : 0;
        return (
          <g key={index} data-baseline-pt={line.baseline.toFixed(2)} data-line-width-pt={line.width.toFixed(2)}>
            {line.pieces
              .filter((piece) => piece.text.trim() !== '')
              .map((piece, at) => (
                <text
                  key={at}
                  x={ptToPx(offset + piece.x, scale)}
                  y={ptToPx(line.baseline, scale)}
                  fontSize={ptToPx(piece.run.size_pt, scale)}
                  style={{
                    fontFamily: editorFontCss(piece.run.font),
                    fontWeight: piece.run.bold ? 700 : 400,
                    fontStyle: piece.run.italic ? 'italic' : 'normal',
                    fill: ANNOTATION_COLOR_HEX[piece.run.color],
                    whiteSpace: 'pre',
                    fontKerning: 'none',
                  }}
                >
                  {piece.text}
                </text>
              ))}
          </g>
        );
      })}
    </g>
  );
}

export function renderStamp(item: StampAnnotation, scale: number) {
  const layout = layoutStamp(item);
  const hex = ANNOTATION_COLOR_HEX[item.color];
  const px = (value: number) => ptToPx(value, scale);
  const inset = layout.border / 2;
  return (
    <g
      key={item.id}
      className="swm-ann__stamp"
      data-testid={`ann-stamp-${item.id}`}
      data-ann-id={item.id}
      data-label={layout.label.text}
      data-date={item.date_text ?? undefined}
      transform={boxTransform(item, scale)}
      opacity={item.opacity ?? 1}
    >
      {item.border && (
        <rect
          x={px(inset)}
          y={px(inset)}
          width={px(item.w_pt - layout.border)}
          height={px(item.h_pt - layout.border)}
          rx={px(layout.radius)}
          fill="none"
          stroke={hex}
          strokeWidth={px(layout.border)}
        />
      )}
      <text
        x={px(layout.label.x)}
        y={px(layout.label.baseline)}
        fontSize={px(layout.label.size)}
        style={{ fontFamily: editorFontCss('LiberationSans'), fontWeight: 700, fill: hex, whiteSpace: 'pre' }}
      >
        {layout.label.text}
      </text>
      {layout.date && (
        <text
          x={px(layout.date.x)}
          y={px(layout.date.baseline)}
          fontSize={px(layout.date.size)}
          data-testid={`ann-stamp-date-${item.id}`}
          style={{ fontFamily: editorFontCss('LiberationSans'), fill: hex, whiteSpace: 'pre' }}
        >
          {layout.date.text}
        </text>
      )}
    </g>
  );
}

/** The text box editor (a textarea over the box, in its face and size). */
export interface TextboxEditorState {
  id: string | null;
  page: number;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  value: string;
  font: TextboxAnnotation['runs'][number]['font'];
  size_pt: number;
  color: TextboxAnnotation['runs'][number]['color'];
  bold: boolean;
  italic: boolean;
  align: TextboxAnnotation['align'];
  line_height: number;
  error?: string | null;
}

export function TextboxEditor({
  editor,
  scale,
  onChange,
  onCommit,
  onCancel,
}: {
  editor: TextboxEditorState;
  scale: number;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const key = `${editor.id ?? 'new'}:${editor.page}:${editor.x_pt}:${editor.y_pt}`;
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.focus({ preventScroll: true });
    element.setSelectionRange(element.value.length, element.value.length);
  }, [key]);
  // Grow with the text (the box's height follows its lines on commit).
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = '0px';
    element.style.height = `${element.scrollHeight}px`;
  });
  const size = ptToPx(editor.size_pt, scale);
  const style: CSSProperties = {
    left: ptToPx(editor.x_pt, scale),
    top: ptToPx(editor.y_pt, scale),
    width: ptToPx(editor.w_pt, scale),
    fontFamily: editorFontCss(editor.font),
    fontSize: size,
    lineHeight: `${editor.size_pt * editor.line_height * scale}px`,
    fontWeight: editor.bold ? 700 : 400,
    fontStyle: editor.italic ? 'italic' : 'normal',
    color: ANNOTATION_COLOR_HEX[editor.color],
    textAlign: editor.align === 'justify' ? 'left' : editor.align,
  };
  return (
    <>
      <textarea
        key={key}
        ref={ref}
        className={`swm-textbox-editor${editor.error ? ' is-invalid' : ''}`}
        data-testid="textbox-editor"
        data-font={editor.font}
        data-size-pt={editor.size_pt}
        aria-label={editor.id ? 'Edit the text box' : 'Text of the new text box'}
        aria-invalid={editor.error ? true : undefined}
        title={`${EDITOR_FONT_LABEL[editor.font]} ${editor.size_pt} pt — Enter saves, Shift+Enter new line, Esc cancels`}
        value={editor.value}
        maxLength={2000}
        rows={1}
        style={style}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            onCommit();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            onCancel();
          }
        }}
        onPaste={(event) => {
          // Plain text only (NFC), never the clipboard's formatting.
          const text = event.clipboardData.getData('text/plain');
          if (!text) return;
          event.preventDefault();
          const element = event.currentTarget;
          const start = element.selectionStart;
          const end = element.selectionEnd;
          const next = `${element.value.slice(0, start)}${text.normalize('NFC')}${element.value.slice(end)}`;
          onChange(next.slice(0, 2000));
        }}
        onBlur={(event) => {
          const next = event.relatedTarget as Element | null;
          if (next?.closest?.('.ws-props__group--appearance, .ws-props__group--textbox, .ws-color__palette, .tb-font, .modal')) return;
          onCommit();
        }}
        onClick={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
      />
      {editor.error && (
        <span
          className="swm-ann-input-note swm-ann-input-note--error"
          data-testid="textbox-editor-error"
          role="alert"
          style={{ left: ptToPx(editor.x_pt, scale), top: ptToPx(editor.y_pt, scale) - 22 }}
        >
          {editor.error}
        </span>
      )}
    </>
  );
}
