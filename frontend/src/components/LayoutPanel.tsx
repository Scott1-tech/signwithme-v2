import { useState } from 'react';
import { isEmbeddedFamily } from '../lib/fonts';
import {
  DATE_FONTS,
  DATE_FONT_LABEL,
  DATE_FONT_SIZE_MAX,
  DATE_FONT_SIZE_MIN,
  SIGNATURE_SCALE_MAX,
  SIGNATURE_SCALE_MIN,
  clampFontSize,
  clampScale,
  isDefaultLayout,
  withDateStyle,
  withSlotsShifted,
  type DateFont,
  type FinalizeLayout,
  type ScaleRange,
  type SlotBounds,
} from '../lib/layout';

export interface LayoutSlotRow {
  fieldId: string;
  label: string;
  kind: 'signature' | 'date';
  page: number | null;
  /** How far this slot may move and stay on its page; absent while the page size is unknown. */
  bounds?: SlotBounds;
  /** Signature rows only: the size this slot prints at (its own, else the global). */
  scale?: number;
  /** Signature rows only: how large it may be drawn and stay on its page. */
  scaleBounds?: ScaleRange;
  /** This slot has a size of its own, so the global slider no longer moves it. */
  ownScale?: boolean;
}

interface Props {
  layout: FinalizeLayout;
  /**
   * A user change of the layout -- one undo step of the workspace history (W2b).
   * `meta.coalesce` merges a run of changes into one step (a slider dragged
   * across its track); `meta.label` names the step.
   */
  onChange: (next: FinalizeLayout, meta?: { label: string; coalesce?: string }) => void;
  onReset: () => void;
  rows: LayoutSlotRow[];
  disabled?: boolean;
  /** Kept for callers; per-slot size is changed with the corner handle on the page. */
  onSlotScaleStep?: (fieldId: string, direction: 1 | -1, large: boolean) => void;
  /** Kept for callers; see `onSlotScaleStep`. */
  onSlotScaleClear?: (fieldId: string) => void;
}

type Target = 'signature' | 'date' | 'all';
const TARGETS: Array<{ id: Target; label: string }> = [
  { id: 'signature', label: 'Signatures' },
  { id: 'date', label: 'Dates' },
  { id: 'all', label: 'All' },
];
const STEPS = [0.5, 2, 10] as const;

/* The mockup's icons, 16 px, drawn with the current text colour. */
const Chevron = ({ d }: { d: string }) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);
/**
 * "Placement", in the Placement panel design: move the selected mark or a
 * whole group with the nudge pad, pick a mark from the list, and set the date
 * font and the sizes. The page preview follows live; settings apply when the
 * final contract is generated.
 */
export function LayoutPanel({ layout, onChange, onReset, rows, disabled }: Props) {
  const [target, setTarget] = useState<Target>('all');
  const [step, setStep] = useState<(typeof STEPS)[number]>(2);

  const targets = rows
    .filter((row) => target === 'all' || row.kind === target)
    .map((row) => ({ fieldId: row.fieldId, bounds: row.bounds }));
  const hasSignatures = rows.some((row) => row.kind === 'signature');
  const hasDates = rows.some((row) => row.kind === 'date');

  const nudge = (dx: number, dy: number) => {
    const next = withSlotsShifted(layout, targets, { dx, dy });
    if (next !== layout) onChange(next, { label: 'Move marks' });
  };
  const canNudge = !disabled && targets.length > 0;
  return (
    <div className="pp" data-testid="layout-panel">
      <div className="pp-head">
        <p className="pp-sub">Drag a mark on the page or nudge it here.</p>
        <button
          type="button"
          className="pp-link"
          disabled={disabled || isDefaultLayout(layout)}
          onClick={onReset}
        >
          Reset
        </button>
      </div>

      {rows.length > 0 && (
        <section className="pp-section" aria-labelledby="pp-move-h">
          <p id="pp-move-h" className="pp-title">Move</p>
          <div className="pp-seg" role="radiogroup" aria-labelledby="pp-move-h">
            {TARGETS.map((item) => {
              const off =
                (item.id === 'signature' && !hasSignatures) || (item.id === 'date' && !hasDates);
              return (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={target === item.id}
                  className="pp-seg__item"
                  disabled={off}
                  onClick={() => setTarget(item.id)}
                >
                  {item.label}
                </button>
              );
            })}
          </div>

          <div className="pp-move">
            <div className="pp-pad" role="group" aria-label="Nudge">
              <span />
              <button type="button" className="pp-pad__btn" aria-label="Nudge up" disabled={!canNudge}
                onClick={() => nudge(0, step)}>
                <Chevron d="M4 10l4-4 4 4" />
              </button>
              <span />
              <button type="button" className="pp-pad__btn" aria-label="Nudge left" disabled={!canNudge}
                onClick={() => nudge(-step, 0)}>
                <Chevron d="M10 4l-4 4 4 4" />
              </button>
              <span className="pp-pad__dot" aria-hidden="true"><span /></span>
              <button type="button" className="pp-pad__btn" aria-label="Nudge right" disabled={!canNudge}
                onClick={() => nudge(step, 0)}>
                <Chevron d="M6 4l4 4-4 4" />
              </button>
              <span />
              <button type="button" className="pp-pad__btn" aria-label="Nudge down" disabled={!canNudge}
                onClick={() => nudge(0, -step)}>
                <Chevron d="M4 6l4 4 4-4" />
              </button>
              <span />
            </div>

          </div>

          <div className="pp-step">
            <span id="pp-step-h" className="pp-label">Step</span>
            <div className="pp-seg pp-seg--step" role="radiogroup" aria-labelledby="pp-step-h">
              {STEPS.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={step === value}
                  className="pp-seg__item pp-seg__item--mono"
                  onClick={() => setStep(value)}
                >
                  {value} pt
                </button>
              ))}
            </div>
          </div>
        </section>
      )}

      <section className="pp-section pp-section--last" aria-labelledby="pp-text-h">
        <p id="pp-text-h" className="pp-title">Text</p>
        <div className="pp-grid">
          <label htmlFor="layout-date-font" className="pp-label">Font</label>
          <div className="pp-font">
            <select
              id="layout-date-font"
              className="pp-select"
              data-testid="layout-date-font"
              value={layout.date_font}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...layout, date_font: event.target.value as DateFont }, { label: 'Date font' })
              }
            >
              <optgroup label="Standard">
                {DATE_FONTS.filter((font) => !isEmbeddedFamily(font)).map((font) => (
                  <option key={font} value={font}>{DATE_FONT_LABEL[font]}</option>
                ))}
              </optgroup>
              <optgroup label="Embedded">
                {DATE_FONTS.filter(isEmbeddedFamily).map((font) => (
                  <option key={font} value={font}>{DATE_FONT_LABEL[font]}</option>
                ))}
              </optgroup>
            </select>
            <button
              type="button"
              className="pp-square pp-square--bold"
              data-testid="layout-date-bold"
              aria-label="Bold"
              aria-pressed={Boolean(layout.date_bold)}
              disabled={disabled}
              onClick={() => onChange(withDateStyle(layout, { bold: !layout.date_bold }), { label: 'Date bold' })}
            >
              B
            </button>
            <button
              type="button"
              className="pp-square pp-square--italic"
              data-testid="layout-date-italic"
              aria-label="Italic"
              aria-pressed={Boolean(layout.date_italic)}
              disabled={disabled}
              onClick={() =>
                onChange(withDateStyle(layout, { italic: !layout.date_italic }), { label: 'Date italic' })
              }
            >
              I
            </button>
          </div>

          <label htmlFor="pp-dsize" className="pp-label">Date size</label>
          <div className="pp-range">
            <input
              id="pp-dsize"
              type="range"
              min={DATE_FONT_SIZE_MIN}
              max={DATE_FONT_SIZE_MAX}
              step={0.5}
              value={layout.date_font_size}
              disabled={disabled}
              onChange={(event) =>
                onChange(
                  { ...layout, date_font_size: clampFontSize(Number(event.target.value)) },
                  { label: 'Date size', coalesce: 'date_font_size' },
                )
              }
            />
            <span className="pp-range__value">{layout.date_font_size} pt</span>
          </div>

          <label htmlFor="pp-ssize" className="pp-label">Signature size</label>
          <div className="pp-range">
            <input
              id="pp-ssize"
              type="range"
              min={Math.round(SIGNATURE_SCALE_MIN * 100)}
              max={Math.round(SIGNATURE_SCALE_MAX * 100)}
              step={5}
              value={Math.round(layout.signature_scale * 100)}
              disabled={disabled}
              onChange={(event) =>
                onChange(
                  { ...layout, signature_scale: clampScale(Number(event.target.value) / 100) },
                  { label: 'Signature size', coalesce: 'signature_scale' },
                )
              }
            />
            <span className="pp-range__value">{Math.round(layout.signature_scale * 100)}%</span>
          </div>
        </div>
      </section>
    </div>
  );
}
