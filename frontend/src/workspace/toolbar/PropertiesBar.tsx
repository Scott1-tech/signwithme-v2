import { useEffect, useRef, useState } from 'react';
import {
  ANNOTATION_COLORS,
  ANNOTATION_COLOR_HEX,
  ANNOTATION_COLOR_LABEL,
  ANNOTATION_FONT_LABEL,
  MAX_ANNOTATIONS,
  STROKE_MAX_PT,
  STROKE_MIN_PT,
  STROKE_STEP_PT,
  clampStroke,
  isBoxKind,
  isDrawKind,
  isShapeKind,
  isStrokeKind,
  sizeLimits,
  type AnnotationColor,
  type AnnotationFont,
  type AnnotationKind,
} from '../../lib/annotations';
import { FONT_FAMILIES, isEmbeddedFamily } from '../../lib/fonts';
import type { EditorFontFamilyId } from '../../api/document';
import { LINE_HEIGHTS } from '../../lib/annotationKinds/v7';
import { FontPicker } from '../text/FontPicker';
import type { V7Props } from '../tools/types';
import { Icon } from './icons';

/**
 * The properties half of the toolbar (plan §2.6): what the armed tool will
 * place with -- or, with an annotation selected, what THAT annotation is.
 *
 * The boxes have two jobs at once: with something selected they change it
 * (one undo step), and with nothing selected they set what the next one will
 * be placed with. The accessible name says which, so a control is never
 * ambiguous.
 *
 * It is drawn as GROUPS (`appearance`, `date`, `edit`, `history`, `status`),
 * so the toolbar can move the last ones into its «More ▾» menu when the
 * column is too narrow, and into a pop-over in the vertical / mobile panel.
 * Every testid of the old toolbar is kept: `ann-font`, `ann-size`,
 * `ann-date`, `ann-delete`, `editor-undo`, `editor-redo`, `undo-depth`,
 * `redo-depth`, `ann-count`, `sig-count`; new: `ann-color`,
 * `ann-color-palette`, `ann-color-<name>`.
 *
 * WHY THE SIZE BOX MOVES ITS OWN CEILING
 * --------------------------------------
 * A run of text is 6…48 pt and a tick or a cross 6…72, because a tick has to
 * fill a tick-box on a scanned form. The field takes its `min` / `max` from
 * whatever it currently describes (`sizeLimits`), so what the spinner offers
 * is exactly what the server will accept. The font box is the mirror image: a
 * mark has no font, so with one selected the control is disabled and says why.
 */

/** Why the font box goes grey. One place, so the check can pin it. */
export const MARK_FONT_NOTE = 'A check or cross has no font';
/** Why the font / size boxes go grey with a whiteout selected. */
export const WHITEOUT_NOTE = 'A whiteout has no font or size';
/** ... and with a highlight / underline / strikeout (W3): only its colour. */
export const MARKUP_NOTE = 'A highlight, underline or strikeout has no font or size — only a colour';
/** The two groups of the font list (contract v6). */
const STANDARD_FONTS_LABEL = 'Standard (Latin only)';
const EMBEDDED_FONTS_LABEL = 'Embedded (+ Cyrillic, oʻ gʻ)';
/** Why the palette goes grey while the Signature tool is armed. */
export const NO_COLOR_NOTE = 'A signature has no colour';
/** ... and for a picture (W4). */
export const IMAGE_COLOR_NOTE = 'An image has no colour';
/** ... and for a whiteout (W5): always opaque white, never a palette colour. */
export const WHITEOUT_COLOR_NOTE = 'A whiteout is always opaque white';

export type PropertyGroupId =
  | 'appearance'
  | 'textbox'
  | 'stroke'
  | 'fill'
  | 'v7'
  | 'date'
  | 'edit'
  | 'history'
  | 'status';
/** Drawn in this order; the «More ▾» menu takes them from the end. */
export const PROPERTY_GROUPS: readonly PropertyGroupId[] = [
  'appearance',
  'textbox',
  'stroke',
  'fill',
  'v7',
  'date',
  'edit',
  'history',
  'status',
];

/** F2: the kinds with a v7 look («Style ▾»: dash, fill colour, opacity, heads, rotation). */
const V7_STYLE_KINDS: ReadonlySet<AnnotationKind> = new Set([
  'rect', 'ellipse', 'line', 'arrow', 'ink', 'image', 'textbox', 'stamp',
]);
/** ... and the ones a Duplicate makes sense for. */
export const DUPLICABLE: ReadonlySet<AnnotationKind> = new Set([
  'textbox', 'stamp', 'image', 'rect', 'ellipse', 'line', 'arrow', 'check', 'cross', 'square_mark', 'text', 'note',
]);

/**
 * The groups that apply to what the boxes describe (W4): `stroke` only for
 * ink / shapes / lines, `fill` only for a rect / ellipse. A drawing's
 * `appearance` is its colour alone (no font, no size -- they would only be
 * two more disabled boxes in a row that has no room for them).
 */
export function propertyGroupsFor(kind: AnnotationKind, editor = false): readonly PropertyGroupId[] {
  return PROPERTY_GROUPS.filter(
    (id) =>
      (id !== 'stroke' || isStrokeKind(kind)) &&
      // F2: with the editor the v7 «Style ▾» holds the fill (a colour, or none).
      (id !== 'fill' || (isShapeKind(kind) && !editor)) &&
      (id !== 'textbox' || kind === 'textbox') &&
      // F2: with the editor's longer row the Date box shows only for a date
      // (the Date tool still asks for it before the click).
      (id !== 'date' || !editor || kind === 'date') &&
      (id !== 'v7' || (editor && V7_STYLE_KINDS.has(kind))),
  );
}

export interface PropertiesBarProps {
  /** What the boxes currently describe: the selection, else the armed tool. */
  kind: AnnotationKind;
  font: AnnotationFont;
  /** v6: bold / italic of what the boxes describe. */
  bold: boolean;
  italic: boolean;
  onStyle: (style: { bold?: boolean; italic?: boolean }) => void;
  size: number;
  onFont: (font: AnnotationFont) => void;
  onSize: (size: number) => void;
  /** The date the Date tool will place, ISO `yyyy-mm-dd`. */
  date: string;
  onDate: (value: string) => void;
  color: AnnotationColor;
  colorDisabled: boolean;
  onColor: (color: AnnotationColor) => void;
  /** W4: stroke width (pt) of the selected drawing, else of the next one. */
  stroke: number;
  onStroke: (stroke: number) => void;
  /** W4: translucent fill of the selected rect / ellipse, else of the next one. */
  fill: boolean;
  onFill: (fill: boolean) => void;
  /** True while an annotation is selected: the boxes then edit it. */
  hasSelection: boolean;
  onDelete: () => void;
  canUndo: boolean;
  canRedo: boolean;
  undoDepth: number;
  redoDepth: number;
  undoLabel: string | null;
  redoLabel: string | null;
  onUndo: () => void;
  onRedo: () => void;
  /** How many annotations this contract carries. */
  count: number;
  /** Free signatures: NOT in `count` (a server row, not an annotation). */
  signatureCount: number;
  /** F2: the server has the document editor (the v7 controls exist). */
  editor?: boolean;
  /** F2: the text box's family and its picker. */
  boxFont?: EditorFontFamilyId;
  onBoxFont?: (font: EditorFontFamilyId) => void;
  /** F2: the v7 look of the selection / the next drawing. */
  v7?: V7Props & { rotation: number };
  onV7?: (change: Partial<V7Props> & { rotation?: number }) => void;
  /** F2: Duplicate the selection. */
  onDuplicate?: () => void;
}

interface Props extends PropertiesBarProps {
  groups: readonly PropertyGroupId[];
  /** `bar`: one row of groups; `stack`: a column (menu, pop-over). */
  variant?: 'bar' | 'stack';
}

export function PropertiesBar({ groups, variant = 'bar', ...props }: Props) {
  return (
    <>
      {groups.map((id) => (
        <div
          key={id}
          className={`editor__group ws-props__group ws-props__group--${id}${
            variant === 'stack' ? ' ws-props__group--stack' : ''
          }`}
          role="group"
          aria-label={GROUP_LABEL[id]}
          data-group={id}
        >
          <GroupBody id={id} {...props} />
        </div>
      ))}
    </>
  );
}

const GROUP_LABEL: Record<PropertyGroupId, string> = {
  appearance: 'Appearance',
  textbox: 'Text box',
  v7: 'Style',
  stroke: 'Stroke',
  fill: 'Fill',
  date: 'Date to place',
  edit: 'Selection',
  history: 'History',
  status: 'Counts',
};

function GroupBody({ id, ...props }: PropertiesBarProps & { id: PropertyGroupId }) {
  switch (id) {
    case 'appearance':
      return <Appearance {...props} />;
    case 'textbox':
      return <TextboxControls {...props} />;
    case 'v7':
      return <V7Style {...props} />;
    case 'stroke':
      return <StrokeField {...props} />;
    case 'fill':
      return (
        <button
          type="button"
          className={`editor__button ws-fill${props.fill ? ' is-active' : ''}`}
          data-testid="ann-fill"
          aria-pressed={props.fill}
          aria-label={
            props.hasSelection
              ? 'Translucent fill of the selected shape'
              : 'Translucent fill for the next shape'
          }
          title="Fill the shape with its colour at 20 % opacity (never opaque)"
          onClick={() => props.onFill(!props.fill)}
        >
          <span
            className="ws-fill__swatch"
            style={{ background: props.fill ? ANNOTATION_COLOR_HEX[props.color] : 'transparent' }}
            aria-hidden="true"
          />
          <span className="editor__button-word">Fill</span>
        </button>
      );
    case 'date':
      return (
        // Always on the toolbar, armed or not: the point of the tool is that
        // the date is decided *before* the click.
        <label className="editor__field">
          <span className="editor__field-label">Date</span>
          <input
            type="date"
            className="editor__date"
            data-testid="ann-date"
            aria-label="Date the Date tool places"
            title="Date the Date tool places"
            value={props.date}
            onChange={(event) => props.onDate(event.target.value)}
          />
        </label>
      );
    case 'edit':
      return (
        <>
        {props.editor && props.onDuplicate && props.hasSelection && DUPLICABLE.has(props.kind) && (
          <button
            type="button"
            className="editor__button"
            data-testid="ann-duplicate"
            aria-label="Duplicate the selected annotation"
            title="Duplicate (a copy 12 pt down and right, selected)"
            onClick={props.onDuplicate}
          >
            <span className="editor__button-glyph" aria-hidden="true">⧉</span>
            <span className="editor__button-word"> Duplicate</span>
          </button>
        )}
        <button
          type="button"
          className="editor__button"
          data-testid="ann-delete"
          disabled={!props.hasSelection}
          aria-label="Delete the selected annotation"
          title="Delete the selected annotation (Delete)"
          onClick={props.onDelete}
        >
          {/* W5: in a compact row only the glyph is left (like Undo / Redo),
              so 19 tools and a signature count still fit one row at 1600 px. */}
          <span className="editor__button-glyph" aria-hidden="true">
            <Icon name="delete" />
          </span>
          <span className="editor__button-word"> Delete</span>
        </button>
        </>
      );
    case 'history':
      return <HistoryControls {...props} />;
    case 'status':
      return (
        <>
          <span className="editor__status" data-testid="ann-count">
            {props.count} / {MAX_ANNOTATIONS}
          </span>
          {props.signatureCount > 0 && (
            <span className="editor__status" data-testid="sig-count">
              {props.signatureCount} signature{props.signatureCount === 1 ? '' : 's'}
            </span>
          )}
        </>
      );
    default:
      return null;
  }
}

/**
 * Undo / Redo (`editor-undo` / `editor-redo`, depths `undo-depth` /
 * `redo-depth`). F1a: in a wide row the toolbar draws them right after the
 * tools («… | Shapes ▼ | Undo | Redo», EDITOR-V2-PLAN §7) instead of as a
 * property group, so they never go under «More ▾»; in the vertical / mobile
 * pop-over they are still the `history` group.
 */
export function HistoryControls(props: Pick<
  PropertiesBarProps,
  'canUndo' | 'canRedo' | 'undoDepth' | 'redoDepth' | 'undoLabel' | 'redoLabel' | 'onUndo' | 'onRedo'
>) {
  return (
    <>
      <button
        type="button"
        className="editor__button"
        data-testid="editor-undo"
        disabled={!props.canUndo}
        aria-label="Undo — annotations and slot positions; a placed signature is not in this history"
        title={`Undo${props.undoLabel ? `: ${props.undoLabel}` : ''} (Ctrl+Z). Annotations and slot positions — a placed signature is removed with its own button.`}
        onClick={props.onUndo}
      >
        <Icon name="undo" />
        <span className="editor__button-word">Undo</span>
        <span className="editor__count" data-testid="undo-depth">
          {props.undoDepth}
        </span>
      </button>
      <button
        type="button"
        className="editor__button"
        data-testid="editor-redo"
        disabled={!props.canRedo}
        aria-label="Redo — annotations and slot positions; a placed signature is not in this history"
        title={`Redo${props.redoLabel ? `: ${props.redoLabel}` : ''} (Ctrl+Shift+Z or Ctrl+Y)`}
        onClick={props.onRedo}
      >
        <Icon name="redo" />
        <span className="editor__button-word">Redo</span>
        <span className="editor__count" data-testid="redo-depth">
          {props.redoDepth}
        </span>
      </button>
    </>
  );
}

function Appearance({
  kind,
  font,
  bold,
  italic,
  onStyle,
  size,
  onFont,
  onSize,
  color,
  colorDisabled,
  onColor,
  hasSelection,
  boxFont,
  onBoxFont,
}: PropertiesBarProps) {
  const [minSize, maxSize] = sizeLimits(kind);
  // A whiteout, and a highlight / underline / strikeout (W3): no font, no size.
  const isBox = isBoxKind(kind);
  const boxNote = kind === 'whiteout' ? WHITEOUT_NOTE : MARKUP_NOTE;
  const fontDisabled = kind === 'check' || kind === 'cross' || kind === 'square_mark' || isBox;
  const fontNote = isBox ? boxNote : MARK_FONT_NOTE;
  const embedded = FONT_FAMILIES.filter(isEmbeddedFamily);
  const standard = FONT_FAMILIES.filter((name) => !isEmbeddedFamily(name));

  // The size box keeps what is being typed and only reports a value that is
  // already inside the range: clamping every keystroke would turn "2" (on the
  // way to "24") into 6 and leave the user fighting the field.
  const [sizeDraft, setSizeDraft] = useState(String(size));
  useEffect(() => setSizeDraft(String(size)), [size]);

  // F2: a stamp is a colour (and its «New Stamp…» look).
  if (kind === 'stamp') {
    return (
      <ColorPicker color={color} disabled={colorDisabled} hasSelection={hasSelection} onColor={onColor} />
    );
  }

  // W4: a drawing's appearance is its colour alone.
  if (isDrawKind(kind)) {
    return (
      <ColorPicker
        color={color}
        disabled={colorDisabled}
        disabledNote={kind === 'image' ? IMAGE_COLOR_NOTE : NO_COLOR_NOTE}
        hasSelection={hasSelection}
        onColor={onColor}
      />
    );
  }

  return (
    <>
      {kind === 'textbox' && boxFont && onBoxFont ? (
        // F2: a text box is set in any family of `GET /api/fonts` (the same picker as Edit text).
        <span className="editor__field editor__field--font ws-boxfont">
          <span className="editor__field-label">Font</span>
          <FontPicker value={boxFont} original="" substitute={boxFont} onChange={onBoxFont} testid="textbox-font" />
        </span>
      ) : (
      <label className="editor__field editor__field--font">
        <span className="editor__field-label">Font</span>
        <select
          className="editor__select"
          data-testid="ann-font"
          disabled={fontDisabled}
          aria-label={
            fontDisabled
              ? `Font — ${fontNote}`
              : hasSelection
                ? 'Font of the selected annotation'
                : 'Font for the next annotation'
          }
          title={fontDisabled ? fontNote : undefined}
          value={font}
          onChange={(event) => onFont(event.target.value as AnnotationFont)}
        >
          <optgroup label={STANDARD_FONTS_LABEL}>
            {standard.map((name) => (
              <option key={name} value={name}>
                {ANNOTATION_FONT_LABEL[name]}
              </option>
            ))}
          </optgroup>
          <optgroup label={EMBEDDED_FONTS_LABEL}>
            {embedded.map((name) => (
              <option key={name} value={name}>
                {ANNOTATION_FONT_LABEL[name]}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      )}
      <span className="ws-style" role="group" aria-label="Font style">
        <button
          type="button"
          className={`editor__button ws-style__button ws-style__button--bold${bold ? ' is-active' : ''}`}
          data-testid="ann-bold"
          aria-pressed={bold}
          disabled={fontDisabled}
          aria-label={fontDisabled ? `Bold — ${fontNote}` : 'Bold'}
          title={fontDisabled ? fontNote : 'Bold'}
          onClick={() => onStyle({ bold: !bold })}
        >
          B
        </button>
        <button
          type="button"
          className={`editor__button ws-style__button ws-style__button--italic${italic ? ' is-active' : ''}`}
          data-testid="ann-italic"
          aria-pressed={italic}
          disabled={fontDisabled}
          aria-label={fontDisabled ? `Italic — ${fontNote}` : 'Italic'}
          title={fontDisabled ? fontNote : 'Italic'}
          onClick={() => onStyle({ italic: !italic })}
        >
          I
        </button>
      </span>
      <label className="editor__field">
        <span className="editor__field-label">Size</span>
        <input
          type="number"
          className="editor__number"
          data-testid="ann-size"
          data-kind={kind}
          data-min={minSize}
          data-max={maxSize}
          aria-label={
            hasSelection
              ? `Size of the selected annotation, ${minSize} to ${maxSize} pt`
              : `Size for the next annotation, ${minSize} to ${maxSize} pt`
          }
          min={minSize}
          max={maxSize}
          step={1}
          disabled={isBox}
          title={isBox ? boxNote : `Size (${minSize}–${maxSize} pt)`}
          value={sizeDraft}
          onChange={(event) => {
            setSizeDraft(event.target.value);
            const next = Number(event.target.value);
            if (
              event.target.value.trim() !== '' &&
              Number.isFinite(next) &&
              next >= minSize &&
              next <= maxSize
            ) {
              onSize(next);
            }
          }}
          onBlur={() => setSizeDraft(String(size))}
        />
      </label>
      <ColorPicker
        color={color}
        disabled={colorDisabled}
        disabledNote={kind === 'whiteout' ? WHITEOUT_COLOR_NOTE : undefined}
        hasSelection={hasSelection}
        onColor={onColor}
      />
    </>
  );
}

/**
 * W4: the stroke width of ink / shapes / lines, 0.5…12 pt in half points
 * (`ann-stroke`). Like the size box it keeps what is being typed and only
 * reports a value already inside the range.
 */
function StrokeField({ stroke, onStroke, hasSelection }: PropertiesBarProps) {
  const [draft, setDraft] = useState(String(stroke));
  useEffect(() => setDraft(String(stroke)), [stroke]);
  return (
    <label className="editor__field">
      <span className="editor__field-label">Stroke</span>
      <input
        type="number"
        className="editor__number ws-stroke"
        data-testid="ann-stroke"
        data-min={STROKE_MIN_PT}
        data-max={STROKE_MAX_PT}
        min={STROKE_MIN_PT}
        max={STROKE_MAX_PT}
        step={STROKE_STEP_PT}
        aria-label={
          hasSelection
            ? `Stroke width of the selected drawing, ${STROKE_MIN_PT} to ${STROKE_MAX_PT} pt`
            : `Stroke width for the next drawing, ${STROKE_MIN_PT} to ${STROKE_MAX_PT} pt`
        }
        title={`Stroke width (${STROKE_MIN_PT}–${STROKE_MAX_PT} pt)`}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          const next = Number(event.target.value);
          if (
            event.target.value.trim() !== '' &&
            Number.isFinite(next) &&
            next >= STROKE_MIN_PT &&
            next <= STROKE_MAX_PT
          ) {
            onStroke(clampStroke(next));
          }
        }}
        onBlur={() => setDraft(String(stroke))}
      />
    </label>
  );
}

/**
 * One swatch button (`ann-color`) that opens the palette (`ann-color-palette`,
 * seven `ann-color-<name>` swatches, no white). The swatches are painted with
 * the exact hex values the PDF builder prints with.
 */
function ColorPicker({
  color,
  disabled,
  disabledNote = NO_COLOR_NOTE,
  hasSelection,
  onColor,
}: {
  color: AnnotationColor;
  disabled: boolean;
  disabledNote?: string;
  hasSelection: boolean;
  onColor: (color: AnnotationColor) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const name = ANNOTATION_COLOR_LABEL[color];
  const label = disabled
    ? `Colour — ${disabledNote}`
    : hasSelection
      ? `Colour of the selected annotation: ${name}`
      : `Colour for the next annotation: ${name}`;

  return (
    <span className="ws-color" ref={root}>
      <button
        type="button"
        className="editor__button ws-color__button"
        data-testid="ann-color"
        data-color={color}
        aria-label={label}
        title={disabled ? disabledNote : label}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <span
          className="ws-color__swatch"
          style={{ background: ANNOTATION_COLOR_HEX[color] }}
          aria-hidden="true"
        />
        <span className="ws-color__caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <span
          className="ws-color__palette"
          role="listbox"
          aria-label="Colours"
          data-testid="ann-color-palette"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }
          }}
        >
          {ANNOTATION_COLORS.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              aria-selected={option === color}
              className={`ws-color__option${option === color ? ' is-selected' : ''}`}
              data-testid={`ann-color-${option}`}
              data-hex={ANNOTATION_COLOR_HEX[option]}
              aria-label={ANNOTATION_COLOR_LABEL[option]}
              title={`${ANNOTATION_COLOR_LABEL[option]} (${ANNOTATION_COLOR_HEX[option]})`}
              style={{ background: ANNOTATION_COLOR_HEX[option] }}
              onClick={() => {
                onColor(option);
                setOpen(false);
              }}
            />
          ))}
        </span>
      )}
    </span>
  );
}

/**
 * F2: a text box's alignment and line spacing (`ann-align`, `ann-line-height`).
 * Justified text is set ragged-right by the engine (documented); the choice
 * is kept so a later engine can honour it.
 */
function TextboxControls({ v7, onV7, hasSelection }: PropertiesBarProps) {
  if (!v7 || !onV7) return null;
  const whose = hasSelection ? 'of the selected text box' : 'for the next text box';
  return (
    <>
      <select
        className="editor__select ws-align"
        data-testid="ann-align"
        aria-label={`Alignment ${whose}`}
        title="Alignment (justified text is printed ragged-right)"
        value={v7.align}
        onChange={(event) => onV7({ align: event.target.value as V7Props['align'] })}
      >
        <option value="left">Left</option>
        <option value="center">Center</option>
        <option value="right">Right</option>
        <option value="justify">Justify</option>
      </select>
      <label className="editor__field">
        <select
          className="editor__select ws-line-height"
          data-testid="ann-line-height"
          aria-label={`Line spacing ${whose}`}
          title="Line spacing (× the font size)"
          value={String(v7.lineHeight)}
          onChange={(event) => onV7({ lineHeight: Number(event.target.value) })}
        >
          {[...new Set([...LINE_HEIGHTS, v7.lineHeight])].sort((a, b) => a - b).map((value) => (
            <option key={value} value={String(value)}>
              {value.toFixed(2).replace(/0$/, '')}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

/**
 * F2 «Style ▾» (`ann-style` / `ann-style-panel`): the v7 look -- line style
 * (solid / dash / dot), border colour is the palette's, fill colour or none,
 * opacity, arrow heads, rotation -- in one pop-over, so the row still fits at
 * 1600 px.
 */
function V7Style({ kind, v7, onV7, hasSelection }: PropertiesBarProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);
  if (!v7 || !onV7) return null;
  const lines = kind === 'rect' || kind === 'ellipse' || kind === 'line' || kind === 'arrow';
  const fills = kind === 'rect' || kind === 'ellipse';
  const turns = kind === 'image' || kind === 'textbox' || kind === 'stamp';
  const whose = hasSelection ? 'the selection' : 'the next one';
  return (
    <span className="ws-v7" ref={root}>
      <button
        type="button"
        className="editor__button ws-v7__button"
        data-testid="ann-style"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Style of ${whose}: opacity ${Math.round(v7.opacity * 100)} %`}
        title="Style: line, fill, opacity, arrow heads, rotation"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="editor__button-word">Style</span> ▾
      </button>
      {open && (
        <span className="ws-v7__panel" role="dialog" aria-label="Style" data-testid="ann-style-panel">
          {lines && (
            <span className="ws-v7__row" role="group" aria-label="Line style">
              <span className="ws-v7__label">Line</span>
              {(['solid', 'dash', 'dot'] as const).map((dash) => (
                <button
                  key={dash}
                  type="button"
                  className={`editor__button${v7.dash === dash ? ' is-active' : ''}`}
                  data-testid={`ann-dash-${dash}`}
                  aria-pressed={v7.dash === dash}
                  aria-label={`${dash} line`}
                  title={`${dash[0].toUpperCase()}${dash.slice(1)} line`}
                  onClick={() => onV7({ dash })}
                >
                  <svg width="28" height="8" aria-hidden="true">
                    <line x1="2" y1="4" x2="26" y2="4" stroke="currentColor" strokeWidth="2"
                      strokeDasharray={dash === 'dash' ? '6 4' : dash === 'dot' ? '0 4' : undefined}
                      strokeLinecap={dash === 'dot' ? 'round' : 'butt'} />
                  </svg>
                </button>
              ))}
            </span>
          )}
          {fills && (
            <span className="ws-v7__row" role="group" aria-label="Fill">
              <span className="ws-v7__label">Fill</span>
              <button
                type="button"
                className={`editor__button${v7.fillColor === null ? ' is-active' : ''}`}
                data-testid="ann-fill-none"
                aria-pressed={v7.fillColor === null}
                aria-label="No fill"
                title="No fill"
                onClick={() => onV7({ fillColor: null })}
              >
                ∅
              </button>
              {ANNOTATION_COLORS.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={`ws-color__option${v7.fillColor === option ? ' is-selected' : ''}`}
                  data-testid={`ann-fill-color-${option}`}
                  aria-pressed={v7.fillColor === option}
                  aria-label={`Fill ${ANNOTATION_COLOR_LABEL[option]}`}
                  title={`Fill ${ANNOTATION_COLOR_LABEL[option]}`}
                  style={{ background: ANNOTATION_COLOR_HEX[option] }}
                  onClick={() => onV7({ fillColor: option })}
                />
              ))}
            </span>
          )}
          {kind === 'arrow' && (
            <span className="ws-v7__row" role="group" aria-label="Arrow heads">
              <span className="ws-v7__label">Heads</span>
              {(['end', 'start', 'both'] as const).map((heads) => (
                <button
                  key={heads}
                  type="button"
                  className={`editor__button${v7.heads === heads ? ' is-active' : ''}`}
                  data-testid={`ann-heads-${heads}`}
                  aria-pressed={v7.heads === heads}
                  aria-label={`Arrow head at the ${heads === 'both' ? 'both ends' : heads}`}
                  title={heads === 'end' ? '→' : heads === 'start' ? '←' : '↔'}
                  onClick={() => onV7({ heads })}
                >
                  {heads === 'end' ? '→' : heads === 'start' ? '←' : '↔'}
                </button>
              ))}
            </span>
          )}
          <label className="ws-v7__row">
            <span className="ws-v7__label">Opacity</span>
            <select
              className="editor__select"
              data-testid="ann-opacity"
              aria-label={`Opacity of ${whose}`}
              value={String(Math.round(v7.opacity * 100))}
              onChange={(event) => onV7({ opacity: Number(event.target.value) / 100 })}
            >
              {[...new Set([100, 75, 50, 35, 25, 10, Math.round(v7.opacity * 100)])]
                .sort((a, b) => b - a)
                .map((value) => (
                  <option key={value} value={String(value)}>
                    {value} %
                  </option>
                ))}
            </select>
          </label>
          {turns && (
            <span className="ws-v7__row" role="group" aria-label="Rotation">
              <span className="ws-v7__label">Rotate</span>
              {[0, 90, 180, 270].map((rotation) => (
                <button
                  key={rotation}
                  type="button"
                  className={`editor__button${v7.rotation === rotation ? ' is-active' : ''}`}
                  data-testid={`ann-rotate-${rotation}`}
                  aria-pressed={v7.rotation === rotation}
                  aria-label={`Rotate ${rotation}°`}
                  title={`Rotate ${rotation}° clockwise about its centre`}
                  disabled={!hasSelection}
                  onClick={() => onV7({ rotation })}
                >
                  {rotation}°
                </button>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  );
}
