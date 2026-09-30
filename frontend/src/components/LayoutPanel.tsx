import type { MouseEvent } from 'react';
import { isEmbeddedFamily } from '../lib/fonts';
import {
  DATE_FONTS,
  DATE_FONT_LABEL,
  DATE_FONT_SIZE_MAX,
  DATE_FONT_SIZE_MIN,
  OFFSET_LIMIT,
  OFFSET_STEP,
  OFFSET_STEP_LARGE,
  SIGNATURE_SCALE_MAX,
  SLOT_OFFSET_LIMIT,
  SIGNATURE_SCALE_MIN,
  SLOT_SCALE_MAX,
  SLOT_SCALE_MIN,
  SLOT_SCALE_STEP,
  SLOT_SCALE_STEP_LARGE,
  clampFontSize,
  clampOffset,
  clampScale,
  clampToRange,
  formatOffset,
  formatScale,
  isDefaultLayout,
  withDateStyle,
  withSlotOffset,
  withSlotsShifted,
  type DateFont,
  type FinalizeLayout,
  type OffsetRange,
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

interface StepperProps {
  label: string;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  testId?: string;
  /** Symmetric bound; the global nudges stop at ±20 pt, per-slot ones at ±200 pt. */
  limit?: number;
  /** Per-slot only: the page-edge range, narrower than ±limit. */
  range?: OffsetRange;
  /** `vertical`: ▲ (+, up) / ▼. `horizontal`: ◀ / ▶ (+, right). */
  axis?: 'vertical' | 'horizontal';
}

/** ▲ raises the value (moves the print up), ▼ lowers it. Shift = big step. */
function OffsetStepper({
  label,
  value,
  onChange,
  disabled,
  testId,
  limit = OFFSET_LIMIT,
  axis = 'vertical',
  range,
}: StepperProps) {
  const clampValue =
    limit === OFFSET_LIMIT ? clampOffset : (value: number) => clampToRange(value, range ?? null);
  const max = range ? range[1] : limit;
  const min = range ? range[0] : -limit;
  const nudge = (direction: 1 | -1) => (event: MouseEvent<HTMLButtonElement>) =>
    onChange(clampValue(value + direction * (event.shiftKey ? OFFSET_STEP_LARGE : OFFSET_STEP)));
  const horizontal = axis === 'horizontal';
  // Plus is up (vertical) or right (horizontal); minus is down or left.
  const minusWord = horizontal ? 'left' : 'down';
  const plusWord = horizontal ? 'right' : 'up';

  const plus = (
    <button
      type="button"
      className="stepper__button"
      aria-label={`${label}: move ${plusWord}`}
      title={`Move ${plusWord} ${OFFSET_STEP} pt (Shift: ${OFFSET_STEP_LARGE} pt)`}
      disabled={disabled || value >= max}
      onClick={nudge(1)}
    >
      {horizontal ? '▶' : '▲'}
    </button>
  );
  const minus = (
    <button
      type="button"
      className="stepper__button"
      aria-label={`${label}: move ${minusWord}`}
      title={`Move ${minusWord} ${OFFSET_STEP} pt (Shift: ${OFFSET_STEP_LARGE} pt)`}
      disabled={disabled || value <= min}
      onClick={nudge(-1)}
    >
      {horizontal ? '◀' : '▼'}
    </button>
  );

  return (
    <span className="stepper" data-testid={testId}>
      {horizontal ? minus : plus}
      <span className="stepper__value" data-testid={testId ? `${testId}-value` : undefined}>
        {horizontal ? 'x ' : 'y '}
        {formatOffset(value)}
      </span>
      {horizontal ? plus : minus}
    </span>
  );
}

type ShiftChange = { dx?: number; dy?: number };

interface ShiftPadProps {
  label: string;
  testId: string;
  disabled?: boolean;
  /** Whether this move would change at least one slot (else the button is off). */
  canShift: (change: ShiftChange) => boolean;
  onShift: (change: ShiftChange) => void;
}

/**
 * ◀ ▲ ▼ ▶ for a whole group of slots at once. Each press moves every slot of
 * the group by the same step (Shift: the large step); a slot that has reached
 * its page edge stays there. Up is positive dy, right is positive dx -- the
 * same convention as the per-slot steppers and the mouse.
 */
function ShiftPad({ label, testId, disabled, canShift, onShift }: ShiftPadProps) {
  const directions: Array<{ word: string; glyph: string; dx: number; dy: number }> = [
    { word: 'left', glyph: '◀', dx: -1, dy: 0 },
    { word: 'up', glyph: '▲', dx: 0, dy: 1 },
    { word: 'down', glyph: '▼', dx: 0, dy: -1 },
    { word: 'right', glyph: '▶', dx: 1, dy: 0 },
  ];
  return (
    <span className="stepper shift-pad" data-testid={testId}>
      {directions.map(({ word, glyph, dx, dy }) => {
        const unit = { dx: dx * OFFSET_STEP, dy: dy * OFFSET_STEP };
        return (
          <button
            key={word}
            type="button"
            className="stepper__button"
            aria-label={`${label}: move ${word}`}
            title={`Move ${word} ${OFFSET_STEP} pt (Shift: ${OFFSET_STEP_LARGE} pt)`}
            disabled={disabled || !canShift(unit)}
            onClick={(event) => {
              const step = event.shiftKey ? OFFSET_STEP_LARGE : OFFSET_STEP;
              onShift({ dx: dx * step, dy: dy * step });
            }}
          >
            {glyph}
          </button>
        );
      })}
    </span>
  );
}

interface ScaleStepperProps {
  label: string;
  /** The size this slot prints at, own or inherited. */
  value: number;
  /** It is this slot's own size (rather than the global slider's). */
  own: boolean;
  /** What still fits on the page; `null`/absent means only [0.3, 3.0] applies. */
  range?: ScaleRange;
  disabled?: boolean;
  testId: string;
  onStep: (direction: 1 | -1, large: boolean) => void;
  onClear: () => void;
}

/**
 * The keyboard way to do what the corner handle does: − / + in 5 % steps
 * (Shift: 25 %), the current percentage, and "Auto" to hand the slot back to
 * the contract-wide slider. Same clamping as the handle -- the page edge is
 * what `range` carries.
 */
function ScaleStepper({
  label,
  value,
  own,
  range,
  disabled,
  testId,
  onStep,
  onClear,
}: ScaleStepperProps) {
  const max = range ? range[1] : SLOT_SCALE_MAX;
  const min = range ? range[0] : SLOT_SCALE_MIN;
  return (
    <span className="stepper stepper--scale" data-testid={testId}>
      <button
        type="button"
        className="stepper__button"
        aria-label={`${label}: smaller`}
        title={`Smaller by ${Math.round(SLOT_SCALE_STEP * 100)} % (Shift: ${Math.round(
          SLOT_SCALE_STEP_LARGE * 100,
        )} %)`}
        disabled={disabled || value <= min}
        onClick={(event) => onStep(-1, event.shiftKey)}
      >
        −
      </button>
      <span
        className={`stepper__value${own ? ' stepper__value--own' : ''}`}
        data-testid={`${testId}-value`}
        title={own ? 'This slot has its own size' : 'Follows the Signature size slider'}
      >
        {formatScale(value)}
        {own ? '' : ' auto'}
      </span>
      <button
        type="button"
        className="stepper__button"
        aria-label={`${label}: larger`}
        title={`Larger by ${Math.round(SLOT_SCALE_STEP * 100)} % (Shift: ${Math.round(
          SLOT_SCALE_STEP_LARGE * 100,
        )} %)`}
        disabled={disabled || value >= max}
        onClick={(event) => onStep(1, event.shiftKey)}
      >
        +
      </button>
      <button
        type="button"
        className="stepper__button stepper__button--wide"
        aria-label={`${label}: use the global size`}
        title="Back to the Signature size slider"
        disabled={disabled || !own}
        onClick={onClear}
      >
        Auto
      </button>
    </span>
  );
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
  /** One step of a signature slot's own size (clamped to the page by the caller). */
  onSlotScaleStep?: (fieldId: string, direction: 1 | -1, large: boolean) => void;
  /** Hand a slot back to the global `Signature size` slider. */
  onSlotScaleClear?: (fieldId: string) => void;
}

/**
 * "Position & appearance": nudges where signatures and dates print and how
 * they look. Settings apply to the whole contract; the per-slot offsets add
 * on top of the offset for that slot's kind. The page preview follows live.
 */
export function LayoutPanel({
  layout,
  onChange,
  onReset,
  rows,
  disabled,
  onSlotScaleStep,
  onSlotScaleClear,
}: Props) {
  const setSlotOffset = (fieldId: string, change: { dx?: number; dy?: number }) =>
    onChange(withSlotOffset(layout, fieldId, change));
  // How many signature slots the global slider still speaks for, so the row
  // says what it actually does once a slot has been resized on its own.
  const sized = rows.filter((row) => row.kind === 'signature' && row.ownScale).length;

  // "Move together": the same step added to every slot's own dx/dy, each one
  // clamped to its own page (see `withSlotsShifted`).
  const targetsOf = (kind?: 'signature' | 'date') =>
    rows
      .filter((row) => !kind || row.kind === kind)
      .map((row) => ({ fieldId: row.fieldId, bounds: row.bounds }));
  const groups: Array<{ label: string; testId: string; targets: ReturnType<typeof targetsOf> }> = [
    { label: 'Signatures together', testId: 'shift-signatures', targets: targetsOf('signature') },
    { label: 'Dates together', testId: 'shift-dates', targets: targetsOf('date') },
    { label: 'Everything together', testId: 'shift-everything', targets: targetsOf() },
  ];

  return (
    <div className="card" data-testid="layout-panel">
      <div className="card__heading">
        <h2>Position &amp; appearance</h2>
        <button
          type="button"
          className="button button--small"
          disabled={disabled || isDefaultLayout(layout)}
          onClick={onReset}
        >
          Reset
        </button>
      </div>
      <p className="hint">
        Moves signatures and dates on their lines. ▲ moves up by {OFFSET_STEP} pt, Shift+click
        by {OFFSET_STEP_LARGE} pt. “Move together” shifts a whole group in any direction (◀▲▼▶, up
        to ±{SLOT_OFFSET_LIMIT} pt, each mark stops at its page edge). Each slot can also be
        dragged on the page (◀▶▲▼, up to ±{SLOT_OFFSET_LIMIT} pt); double-click a slot to put it
        back. Drag the corner handle of a
        signature to resize just that one ({Math.round(SLOT_SCALE_MIN * 100)}–
        {Math.round(SLOT_SCALE_MAX * 100)} %, its bottom-left corner stays on the line); − and +
        below do the same. Applied when the final contract is generated. Saved in this browser only,
        for this contract.
      </p>

      {rows.length > 0 && (
        <div className="layout-group" data-testid="shift-group">
          <h3>Move together</h3>
          {groups.map((group) => (
            <div key={group.testId} className="layout-row">
              <span className="layout-row__label">
                {group.label.replace(' together', '')}
                <span className="layout-row__meta"> · {group.targets.length}</span>
              </span>
              <ShiftPad
                label={group.label}
                testId={group.testId}
                disabled={disabled || group.targets.length === 0}
                canShift={(change) => withSlotsShifted(layout, group.targets, change) !== layout}
                onShift={(change) => {
                  const next = withSlotsShifted(layout, group.targets, change);
                  if (next !== layout) onChange(next, { label: 'Move together' });
                }}
              />
            </div>
          ))}
        </div>
      )}

      <div className="layout-row">
        <span className="layout-row__label">All signatures</span>
        <OffsetStepper
          label="All signatures"
          testId="offset-all-signatures"
          value={layout.signature_offset_pt}
          disabled={disabled}
          onChange={(value) => onChange({ ...layout, signature_offset_pt: value })}
        />
      </div>
      <div className="layout-row">
        <span className="layout-row__label">All dates</span>
        <OffsetStepper
          label="All dates"
          testId="offset-all-dates"
          value={layout.date_offset_pt}
          disabled={disabled}
          onChange={(value) => onChange({ ...layout, date_offset_pt: value })}
        />
      </div>

      <label className="layout-row">
        <span className="layout-row__label">Signature size</span>
        <input
          type="range"
          aria-label="Signature size"
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
        <span className="stepper__value">{Math.round(layout.signature_scale * 100)}%</span>
      </label>
      {sized > 0 && (
        <p className="hint hint--tight" data-testid="own-size-note">
          {sized === 1
            ? '1 signature has its own size and ignores this slider.'
            : `${sized} signatures have their own size and ignore this slider.`}{' '}
          Use “Auto” on a row below to give it back.
        </p>
      )}

      <div className="layout-row">
        <label className="layout-row__label" htmlFor="layout-date-font">
          Date font
        </label>
        <span className="layout-row__font">
          <select
            id="layout-date-font"
            aria-label="Date font"
            data-testid="layout-date-font"
            value={layout.date_font}
            disabled={disabled}
            onChange={(event) =>
              onChange(
                { ...layout, date_font: event.target.value as DateFont },
                { label: 'Date font' },
              )
            }
          >
            {/* The same six families (two groups) as a free annotation's font box. */}
            <optgroup label="Standard">
              {DATE_FONTS.filter((font) => !isEmbeddedFamily(font)).map((font) => (
                <option key={font} value={font}>
                  {DATE_FONT_LABEL[font]}
                </option>
              ))}
            </optgroup>
            <optgroup label="Embedded">
              {DATE_FONTS.filter(isEmbeddedFamily).map((font) => (
                <option key={font} value={font}>
                  {DATE_FONT_LABEL[font]}
                </option>
              ))}
            </optgroup>
          </select>
          <span className="ws-style" role="group" aria-label="Date style">
            <button
              type="button"
              className={`editor__button ws-style__button ws-style__button--bold${
                layout.date_bold ? ' is-active' : ''
              }`}
              data-testid="layout-date-bold"
              aria-pressed={Boolean(layout.date_bold)}
              aria-label="Date in bold"
              title="Slot dates in bold"
              disabled={disabled}
              onClick={() =>
                onChange(withDateStyle(layout, { bold: !layout.date_bold }), { label: 'Date bold' })
              }
            >
              B
            </button>
            <button
              type="button"
              className={`editor__button ws-style__button ws-style__button--italic${
                layout.date_italic ? ' is-active' : ''
              }`}
              data-testid="layout-date-italic"
              aria-pressed={Boolean(layout.date_italic)}
              aria-label="Date in italic"
              title="Slot dates in italic"
              disabled={disabled}
              onClick={() =>
                onChange(withDateStyle(layout, { italic: !layout.date_italic }), {
                  label: 'Date italic',
                })
              }
            >
              I
            </button>
          </span>
        </span>
      </div>

      <label className="layout-row">
        <span className="layout-row__label">Date size</span>
        <input
          type="range"
          aria-label="Date size"
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
        <span className="stepper__value">{layout.date_font_size} pt</span>
      </label>

      {rows.length > 0 && (
        <div className="layout-slots">
          <h3>Each slot</h3>
          {rows.map((row) => (
            <div key={row.fieldId} className="layout-row" data-testid={`slot-offset-row-${row.fieldId}`}>
              <span className="layout-row__label">
                {row.label}
                <span className="layout-row__meta">
                  {' '}
                  · {row.kind === 'signature' ? 'signature' : 'date'}
                  {row.page ? ` · p.${row.page}` : ''}
                </span>
              </span>
              <span className="layout-row__steppers">
                <OffsetStepper
                  label={`${row.label} (${row.kind})`}
                  testId={`offset-slot-x-${row.fieldId}`}
                  axis="horizontal"
                  limit={SLOT_OFFSET_LIMIT}
                  range={row.bounds?.dx}
                  value={layout.slot_offsets_x_pt[row.fieldId] ?? 0}
                  disabled={disabled}
                  onChange={(value) => setSlotOffset(row.fieldId, { dx: value })}
                />
                <OffsetStepper
                  label={`${row.label} (${row.kind})`}
                  testId={`offset-slot-${row.fieldId}`}
                  limit={SLOT_OFFSET_LIMIT}
                  range={row.bounds?.dy}
                  value={layout.slot_offsets_pt[row.fieldId] ?? 0}
                  disabled={disabled}
                  onChange={(value) => setSlotOffset(row.fieldId, { dy: value })}
                />
                {row.kind === 'signature' && onSlotScaleStep && onSlotScaleClear && (
                  <ScaleStepper
                    label={`${row.label} (${row.kind})`}
                    testId={`slot-scale-${row.fieldId}`}
                    value={row.scale ?? layout.signature_scale}
                    own={Boolean(row.ownScale)}
                    range={row.scaleBounds}
                    disabled={disabled}
                    onStep={(direction, large) => onSlotScaleStep(row.fieldId, direction, large)}
                    onClear={() => onSlotScaleClear(row.fieldId)}
                  />
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
