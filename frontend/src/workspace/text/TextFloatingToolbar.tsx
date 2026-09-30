import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { Align, EditorFontFamilyId, Hex } from '../../api/document';
import { LINE_HEIGHTS } from '../../lib/annotationKinds/v7';
import { FontPicker } from './FontPicker';
import { SIZE_PRESETS, TEXT_COLORS, clampTextSize, type StyleOverride } from './textOps';
import type { TextStyle } from './useServerTextModel';

/**
 * F1b: the floating toolbar of a text edit (EDITOR-V2-PLAN §7):
 *
 *   Bold | Italic | Size ▼ | Font ▼ | Color ▼ | Move | Duplicate | Delete
 *
 * «Link» belongs here too, but the link tool is F2: until it exists there is
 * no Link button (a button that does nothing is worse than none). The
 * buttons never take the focus from the editor (`mousedown` is swallowed), so
 * the caret and the selection stay where they were; the size box and the
 * font search do take it, and give it back when done.
 */

interface Props {
  style: TextStyle;
  originalFont: string;
  substitute: EditorFontFamilyId;
  /** Where to stand, CSS px in the page (above the box; below it near the top). */
  anchor: { left: number; top: number; bottom: number };
  moving: boolean;
  onStyle: (change: StyleOverride) => void;
  onMove: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onDone: () => void;
  /** F2: alignment / line spacing of the block (sent with its `text.replace`). */
  align?: Align;
  lineHeight?: number | null;
  onAlign?: (align: Align) => void;
  onLineHeight?: (lineHeight: number) => void;
  /** F2: «Link» -- a link over this block's box (absent without the link tool). */
  onLink?: () => void;
}

const keepFocus = (event: React.MouseEvent) => event.preventDefault();

export function TextFloatingToolbar({
  style,
  originalFont,
  substitute,
  anchor,
  moving,
  onStyle,
  onMove,
  onDuplicate,
  onDelete,
  onDone,
  align = 'left',
  lineHeight = null,
  onAlign,
  onLineHeight,
  onLink,
}: Props) {
  const [sizeOpen, setSizeOpen] = useState(false);
  const [colorOpen, setColorOpen] = useState(false);
  const [sizeText, setSizeText] = useState(String(style.size));
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => setSizeText(String(style.size)), [style.size]);

  const commitSize = (value: number) => {
    if (!Number.isFinite(value) || value <= 0) return;
    const size = clampTextSize(value);
    // The box shows it at once: a blur right after must not put the old value back.
    setSizeText(String(size));
    if (size !== style.size) onStyle({ size });
  };

  const above = anchor.top - 44;
  const placement: CSSProperties = { left: Math.max(0, anchor.left), top: above >= 0 ? above : anchor.bottom + 6 };

  return (
    <div
      ref={rootRef}
      className="tb-toolbar"
      role="toolbar"
      aria-label="Text"
      data-testid="text-toolbar"
      style={placement}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        className={`tb-btn${style.bold ? ' is-on' : ''}`}
        data-testid="text-bold"
        aria-pressed={style.bold}
        title="Bold (Ctrl+B)"
        onMouseDown={keepFocus}
        onClick={() => onStyle({ bold: !style.bold })}
      >
        <b>B</b>
      </button>
      <button
        type="button"
        className={`tb-btn${style.italic ? ' is-on' : ''}`}
        data-testid="text-italic"
        aria-pressed={style.italic}
        title="Italic (Ctrl+I)"
        onMouseDown={keepFocus}
        onClick={() => onStyle({ italic: !style.italic })}
      >
        <i>I</i>
      </button>

      <div className="tb-size">
        <input
          className="tb-size__input"
          data-testid="text-size"
          aria-label="Font size (pt)"
          inputMode="decimal"
          value={sizeText}
          onChange={(event) => setSizeText(event.target.value)}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === 'Enter') {
              commitSize(Number(sizeText));
              onDone();
            } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
              event.preventDefault();
              const step = event.shiftKey ? 4 : 1;
              commitSize(style.size + (event.key === 'ArrowUp' ? step : -step));
            } else if (event.key === 'Escape') {
              setSizeText(String(style.size));
              onDone();
            }
          }}
          onBlur={(event) => commitSize(Number(event.currentTarget.value))}
        />
        <button
          type="button"
          className="tb-btn tb-size__toggle"
          data-testid="text-size-toggle"
          aria-haspopup="listbox"
          aria-expanded={sizeOpen}
          title="Font size"
          onMouseDown={keepFocus}
          onClick={() => {
            setSizeOpen((current) => !current);
            setColorOpen(false);
          }}
        >
          ▾
        </button>
        {sizeOpen && (
          <ul className="tb-menu" role="listbox" aria-label="Font sizes" data-testid="text-size-menu">
            {SIZE_PRESETS.map((size) => (
              <li key={size}>
                <button
                  type="button"
                  role="option"
                  aria-selected={size === style.size}
                  className={`tb-menu__item${size === style.size ? ' is-current' : ''}`}
                  data-testid={`text-size-${size}`}
                  onMouseDown={keepFocus}
                  onClick={() => {
                    commitSize(size);
                    setSizeOpen(false);
                  }}
                >
                  {size}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <FontPicker
        value={style.family}
        original={originalFont}
        substitute={substitute}
        onChange={(family) => {
          onStyle({ family });
          onDone();
        }}
      />

      <div className="tb-color">
        <button
          type="button"
          className="tb-btn"
          data-testid="text-color"
          data-value={style.color}
          aria-haspopup="listbox"
          aria-expanded={colorOpen}
          title="Colour"
          onMouseDown={keepFocus}
          onClick={() => {
            setColorOpen((current) => !current);
            setSizeOpen(false);
          }}
        >
          <span className="tb-color__swatch" style={{ background: style.color }} aria-hidden="true" /> ▾
        </button>
        {colorOpen && (
          <ul className="tb-menu tb-menu--colors" role="listbox" aria-label="Colours" data-testid="text-color-menu">
            {TEXT_COLORS.map((color) => (
              <li key={color.hex}>
                <button
                  type="button"
                  role="option"
                  aria-selected={color.hex.toLowerCase() === style.color.toLowerCase()}
                  className="tb-menu__item tb-menu__swatch"
                  data-testid={`text-color-${color.name.toLowerCase()}`}
                  title={color.name}
                  onMouseDown={keepFocus}
                  onClick={() => {
                    onStyle({ color: color.hex as Hex });
                    setColorOpen(false);
                  }}
                >
                  <span className="tb-color__swatch" style={{ background: color.hex }} aria-hidden="true" />
                  {color.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {onAlign && (
        <select
          className="tb-btn tb-select"
          data-testid="text-align"
          aria-label="Alignment"
          title="Alignment (justified text is set ragged-right)"
          value={align}
          onChange={(event) => {
            onAlign(event.target.value as Align);
            onDone();
          }}
        >
          <option value="left">Left</option>
          <option value="center">Center</option>
          <option value="right">Right</option>
          <option value="justify">Justify</option>
        </select>
      )}
      {onLineHeight && (
        <select
          className="tb-btn tb-select"
          data-testid="text-line-height"
          aria-label="Line spacing"
          title="Line spacing (× the font size)"
          value={lineHeight ? String(lineHeight) : ''}
          onChange={(event) => {
            if (event.target.value) onLineHeight(Number(event.target.value));
            onDone();
          }}
        >
          <option value="">As it was</option>
          {LINE_HEIGHTS.map((value) => (
            <option key={value} value={String(value)}>
              {value}
            </option>
          ))}
        </select>
      )}
      {onLink && (
        <button
          type="button"
          className="tb-btn"
          data-testid="text-link"
          title="Make this text a link (to a web address or a page)"
          onMouseDown={keepFocus}
          onClick={onLink}
        >
          🔗 Link
        </button>
      )}
      <span className="tb-sep" aria-hidden="true" />
      <button
        type="button"
        className={`tb-btn${moving ? ' is-on' : ''}`}
        data-testid="text-move"
        aria-pressed={moving}
        title="Move — drag the ✥ handle"
        onMouseDown={keepFocus}
        onClick={onMove}
      >
        ✥ Move
      </button>
      <button
        type="button"
        className="tb-btn"
        data-testid="text-duplicate"
        title="Duplicate as a new text annotation"
        onMouseDown={keepFocus}
        onClick={onDuplicate}
      >
        ⧉ Duplicate
      </button>
      <button
        type="button"
        className="tb-btn tb-btn--danger"
        data-testid="text-delete"
        title="Delete this text from the document"
        onMouseDown={keepFocus}
        onClick={onDelete}
      >
        🗑 Delete
      </button>
    </div>
  );
}
