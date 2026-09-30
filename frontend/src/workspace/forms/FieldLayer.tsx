import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import type { Rect } from '../../api/document';
import type { FieldSuggestionOut, FieldType } from '../../api/forms';
import { snapPt } from '../../lib/annotations';
import { frameLayerStyle, unrotateVector, UNROTATED, type PageFrame } from '../../lib/viewport';
import {
  FIELD_TYPE_LABEL,
  MIN_FIELD_PT,
  charsetMessage,
  winAnsiProblem,
  type FieldRecord,
  type FieldValue,
} from './fieldModel';

/**
 * F2b: the form fields of ONE page (EDITOR-V2-PLAN §7.4), a direct child of
 * the page's `.pdf-overlay` (drawn after every other layer, so a field is
 * reachable over a slot or an annotation). The layer itself takes no pointer;
 * what it holds does, by mode (see `useForms`):
 *
 *  * `edit`:   `.fm-field` boxes -- press = select (+ move), 8 handles on the
 *              selected one (`field-handle-<nw|n|ne|e|se|s|sw|w>`); the live
 *              drag is this component's own state, ONE op on release;
 *  * `fill`:   an HTML control over each box (`field-input`); text commits on
 *              Enter / leaving the box (Esc = back), the rest on change; a
 *              character a form field cannot hold is said at once and never sent;
 *  * `detect`: the suggestions as dashed frames (`field-suggestion`), movable
 *              and resizable like a field, ✓ / ✕ on the frame;
 *  * `passive`: outlines only.
 *
 * Geometry: PDF points, top-left, unrotated -- `× scale` on screen, like
 * every other overlay layer; drags snap to the 0.5 pt grid and stay inside the page.
 * ROTFIX: on a page with its own `/Rotate` the layer is turned onto the box
 * (`frameLayerStyle`) and a drag's travel is turned back (`unrotateVector`);
 * `pageSize` is then the UNROTATED page.
 */

export type FormsMode = 'edit' | 'fill' | 'detect' | 'passive';

export interface FieldView {
  record: FieldRecord;
  value: FieldValue | null;
  problem: string | null;
}

export type Suggestion = FieldSuggestionOut;

export interface SuggestionView {
  suggestion: Suggestion;
  picked: boolean;
}

interface Props {
  pageNumber: number;
  scale: number;
  /** ROTFIX: the page's own `/Rotate` + unrotated size (the widgets are the engine's). */
  frame?: PageFrame | null;
  pageSize: { width: number; height: number } | null;
  mode: FormsMode;
  fields: FieldView[];
  suggestions: SuggestionView[];
  selectedKey: string | null;
  canFill: boolean;
  fillReason: string | null;
  onSelect: (key: string | null) => void;
  onRect: (key: string, rect: Rect, resized: boolean) => void;
  onValue: (key: string, value: FieldValue) => Promise<boolean> | boolean | void;
  onSuggestionPick: (id: string | null) => void;
  onSuggestionRect: (id: string, rect: Rect) => void;
  onSuggestionAccept: (id: string) => void;
  onSuggestionReject: (id: string) => void;
}

type Handle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';
const HANDLES: readonly Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

interface Drag {
  id: string;
  kind: 'field' | 'suggestion';
  handle: Handle | null;
  pointerId: number;
  startX: number;
  startY: number;
  start: Rect;
  rect: Rect;
  moved: boolean;
}

const FONT_CSS: Record<string, string> = {
  LiberationSerif: '"Liberation Serif", "Times New Roman", Times, serif',
  LiberationMono: '"Liberation Mono", "Courier New", Courier, monospace',
};
const fontCss = (font: string | null | undefined) =>
  FONT_CSS[font ?? ''] ?? '"Liberation Sans", Helvetica, Arial, sans-serif';

/** The rect after dragging `handle` (null = the whole box) by (dx, dy) pt, inside the page. */
export function dragRect(start: Rect, handle: Handle | null, dx: number, dy: number, page: { width: number; height: number } | null): Rect {
  const width = page?.width ?? 1e5;
  const height = page?.height ?? 1e5;
  if (!handle) {
    const x = Math.min(Math.max(0, snapPt(start.x_pt + dx)), Math.max(0, width - start.w_pt));
    const y = Math.min(Math.max(0, snapPt(start.y_pt + dy)), Math.max(0, height - start.h_pt));
    return { ...start, x_pt: x, y_pt: y };
  }
  let left = start.x_pt;
  let top = start.y_pt;
  let right = start.x_pt + start.w_pt;
  let bottom = start.y_pt + start.h_pt;
  if (handle.includes('w')) left = Math.min(Math.max(0, snapPt(left + dx)), right - MIN_FIELD_PT);
  if (handle.includes('e')) right = Math.max(Math.min(width, snapPt(right + dx)), left + MIN_FIELD_PT);
  if (handle.includes('n')) top = Math.min(Math.max(0, snapPt(top + dy)), bottom - MIN_FIELD_PT);
  if (handle.includes('s')) bottom = Math.max(Math.min(height, snapPt(bottom + dy)), top + MIN_FIELD_PT);
  return { x_pt: left, y_pt: top, w_pt: right - left, h_pt: bottom - top };
}

const rectAttr = (rect: Rect) => `${rect.x_pt},${rect.y_pt},${rect.w_pt},${rect.h_pt}`;

export function FieldLayer({
  pageNumber,
  scale,
  frame = null,
  pageSize,
  mode,
  fields,
  suggestions,
  selectedKey,
  canFill,
  fillReason,
  onSelect,
  onRect,
  onValue,
  onSuggestionPick,
  onSuggestionRect,
  onSuggestionAccept,
  onSuggestionReject,
}: Props) {
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const setLive = (next: Drag | null) => {
    dragRef.current = next;
    setDrag(next);
  };

  const begin = (
    event: ReactPointerEvent<HTMLElement>,
    id: string,
    kind: Drag['kind'],
    rect: Rect,
    handle: Handle | null,
  ) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* capture is a nicety */
    }
    setLive({ id, kind, handle, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, start: rect, rect, moved: false });
  };

  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const current = dragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    event.preventDefault();
    const { x: dx, y: dy } = unrotateVector(
      (event.clientX - current.startX) / scale,
      (event.clientY - current.startY) / scale,
      frame?.rotation ?? 0,
    );
    const moved = current.moved || Math.abs(event.clientX - current.startX) > 2 || Math.abs(event.clientY - current.startY) > 2;
    if (!moved) return;
    setLive({ ...current, moved, rect: dragRect(current.start, current.handle, dx, dy, pageSize) });
  };

  const end = (event: ReactPointerEvent<HTMLElement>, cancel = false) => {
    const current = dragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    setLive(null);
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      /* already released */
    }
    if (cancel || !current.moved) return;
    const same =
      Math.abs(current.rect.x_pt - current.start.x_pt) < 0.25 &&
      Math.abs(current.rect.y_pt - current.start.y_pt) < 0.25 &&
      Math.abs(current.rect.w_pt - current.start.w_pt) < 0.25 &&
      Math.abs(current.rect.h_pt - current.start.h_pt) < 0.25;
    if (same) return;
    if (current.kind === 'field') onRect(current.id, current.rect, current.handle !== null);
    else onSuggestionRect(current.id, current.rect);
  };

  // Esc drops a drag in flight.
  useEffect(() => {
    if (!drag) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !dragRef.current) return;
      event.preventDefault();
      event.stopPropagation();
      setLive(null);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [drag]);

  const boxStyle = (rect: Rect): CSSProperties => ({
    left: rect.x_pt * scale,
    top: rect.y_pt * scale,
    width: rect.w_pt * scale,
    height: rect.h_pt * scale,
  });

  const handlesFor = (id: string, kind: Drag['kind'], rect: Rect) =>
    HANDLES.map((handle) => (
      <span
        key={handle}
        className={`fm-handle fm-handle--${handle}`}
        data-testid={`field-handle-${handle}`}
        aria-hidden="true"
        onPointerDown={(event) => begin(event, id, kind, rect, handle)}
        onPointerMove={move}
        onPointerUp={(event) => end(event)}
        onPointerCancel={(event) => end(event, true)}
      />
    ));

  return (
    <div className={`fm-layer fm-layer--${mode}`} data-testid={`field-layer-${pageNumber}`} data-mode={mode} data-space="unrotated" style={frameLayerStyle(frame, UNROTATED, scale)}>
      {fields.map(({ record, value, problem }) => {
        const def = record.def;
        const live = drag && drag.kind === 'field' && drag.id === record.key ? drag.rect : null;
        const rect = live ?? def.rect;
        const selected = mode === 'edit' && record.key === selectedKey;
        const locked = Boolean(record.lockedReason);
        const other = record.origin?.type === 'other';
        const title =
          (problem ? `${problem} ` : '') +
          (def.tooltip ? `${def.tooltip} — ` : '') +
          `${FIELD_TYPE_LABEL[other ? 'other' : def.type]} «${def.name}»` +
          (def.type === 'radio' && def.export_value ? ` = ${def.export_value}` : '') +
          (def.required ? ' (required)' : '') +
          (locked && mode === 'edit' ? ` — ${record.lockedReason}` : '');
        const common = {
          'data-testid': 'form-field',
          'data-key': record.key,
          'data-name': def.name,
          'data-type': other ? 'other' : def.type,
          'data-source': record.origin ? record.origin.source : 'draft',
          'data-rect-pt': rectAttr(rect),
          'data-export-value': def.export_value ?? undefined,
          'data-selected': selected ? 'true' : undefined,
          'data-locked': locked ? 'true' : undefined,
          'data-problem': problem ?? undefined,
          'data-required': def.required ? 'true' : undefined,
        };
        const classes =
          `fm-field fm-field--${other ? 'other' : def.type}` +
          `${selected ? ' is-selected' : ''}${problem ? ' has-problem' : ''}${def.required ? ' is-required' : ''}` +
          `${locked ? ' is-locked' : ''}${record.origin ? '' : ' is-new'}`;
        if (mode === 'fill' && !other) {
          return (
            <div key={record.key} className={classes} style={boxStyle(rect)} {...common} title={title}>
              <FillControl
                record={record}
                value={value}
                scale={scale}
                pageNumber={pageNumber}
                disabledReason={canFill ? null : fillReason}
                onValue={onValue}
              />
            </div>
          );
        }
        return (
          <div
            key={record.key}
            className={classes}
            style={boxStyle(rect)}
            {...common}
            title={title}
            role={mode === 'edit' ? 'button' : undefined}
            aria-label={mode === 'edit' ? `${FIELD_TYPE_LABEL[other ? 'other' : def.type]} ${def.name}` : undefined}
            aria-pressed={mode === 'edit' ? selected : undefined}
            tabIndex={mode === 'edit' ? -1 : undefined}
            onPointerDown={
              mode === 'edit'
                ? (event) => {
                    // The keys (Delete, arrows) then belong to the field, not to a toolbar button.
                    event.currentTarget.focus({ preventScroll: true });
                    onSelect(record.key);
                    if (!locked) begin(event, record.key, 'field', def.rect, null);
                    else {
                      event.preventDefault();
                      event.stopPropagation();
                    }
                  }
                : undefined
            }
            onPointerMove={mode === 'edit' ? move : undefined}
            onPointerUp={mode === 'edit' ? (event) => end(event) : undefined}
            onPointerCancel={mode === 'edit' ? (event) => end(event, true) : undefined}
            onClick={(event) => event.stopPropagation()}
          >
            <span className="fm-field__label" aria-hidden="true">
              {def.type === 'radio' ? `${def.name}=${def.export_value ?? ''}` : def.name}
            </span>
            {mode !== 'edit' && def.type === 'checkbox' && value === true && <span className="fm-field__tick" aria-hidden="true">✔</span>}
            {selected && !locked && handlesFor(record.key, 'field', def.rect)}
          </div>
        );
      })}
      {suggestions.map(({ suggestion, picked }) => {
        const live = drag && drag.kind === 'suggestion' && drag.id === suggestion.suggestion_id ? drag.rect : null;
        const rect = live ?? suggestion.rect;
        return (
          <div
            key={suggestion.suggestion_id}
            className={`fm-suggestion fm-suggestion--${suggestion.type}${picked ? ' is-picked' : ''}`}
            style={boxStyle(rect)}
            data-testid="field-suggestion"
            data-suggestion-id={suggestion.suggestion_id}
            data-type={suggestion.type}
            data-rect-pt={rectAttr(rect)}
            title={`Suggested ${FIELD_TYPE_LABEL[suggestion.type].toLowerCase()}${suggestion.label ? ` «${suggestion.label}»` : ''} — drag to move, handles to resize; ✓ adds it, ✕ drops it`}
            onPointerDown={(event) => {
              onSuggestionPick(suggestion.suggestion_id);
              begin(event, suggestion.suggestion_id, 'suggestion', suggestion.rect, null);
            }}
            onPointerMove={move}
            onPointerUp={(event) => end(event)}
            onPointerCancel={(event) => end(event, true)}
            onClick={(event) => event.stopPropagation()}
          >
            <span className="fm-suggestion__actions">
              <button
                type="button"
                className="fm-suggestion__btn fm-suggestion__btn--accept"
                data-testid="suggestion-accept"
                aria-label={`Accept the suggested ${FIELD_TYPE_LABEL[suggestion.type].toLowerCase()}`}
                title="Accept: add this field to the draft"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  onSuggestionAccept(suggestion.suggestion_id);
                }}
              >
                ✓
              </button>
              <button
                type="button"
                className="fm-suggestion__btn fm-suggestion__btn--reject"
                data-testid="suggestion-reject"
                aria-label="Reject this suggestion"
                title="Reject: drop this suggestion (nothing is added)"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  onSuggestionReject(suggestion.suggestion_id);
                }}
              >
                ✕
              </button>
            </span>
            {picked && handlesFor(suggestion.suggestion_id, 'suggestion', suggestion.rect)}
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Fill mode: one HTML control per field                                        */
/* -------------------------------------------------------------------------- */

interface FillProps {
  record: FieldRecord;
  value: FieldValue | null;
  scale: number;
  pageNumber: number;
  disabledReason: string | null;
  onValue: (key: string, value: FieldValue) => Promise<boolean> | boolean | void;
}

function FillControl({ record, value, scale, pageNumber, disabledReason, onValue }: FillProps) {
  const def = record.def;
  const type: FieldType = def.type;
  const readOnly = Boolean(def.readonly);
  const blocked = disabledReason ?? (readOnly ? 'This field is read-only' : null);
  const auto = Math.min(12, Math.max(6, def.rect.h_pt * (type === 'multiline' ? 0.4 : 0.62)));
  const style: CSSProperties = {
    fontSize: `${(def.size_pt ?? auto) * scale}px`,
    fontFamily: fontCss(def.font),
  };
  const label = `${def.tooltip || def.name}${def.required ? ' (required)' : ''}`;

  if (type === 'signature') {
    return (
      <span className="fm-fill fm-fill--signature" data-testid="field-input" data-kind="signature" title="A signature field is signed, not filled here">
        Signature
      </span>
    );
  }
  if (type === 'checkbox') {
    return (
      <input
        type="checkbox"
        className="fm-fill fm-fill--check"
        data-testid="field-input"
        data-kind="checkbox"
        aria-label={label}
        aria-required={def.required || undefined}
        checked={value === true}
        disabled={Boolean(blocked)}
        title={blocked ?? label}
        onChange={(event) => void onValue(record.key, event.target.checked)}
      />
    );
  }
  if (type === 'radio') {
    const ev = def.export_value ?? '';
    return (
      <input
        type="radio"
        className="fm-fill fm-fill--check"
        data-testid="field-input"
        data-kind="radio"
        name={`fm-${pageNumber}-${def.name}`}
        aria-label={`${label}: ${ev}`}
        checked={value === ev && ev !== ''}
        disabled={Boolean(blocked)}
        title={blocked ?? `${label}: ${ev}`}
        onChange={() => void onValue(record.key, ev)}
      />
    );
  }
  if (type === 'dropdown') {
    const current = typeof value === 'string' ? value : '';
    return (
      <select
        className="fm-fill fm-fill--select"
        data-testid="field-input"
        data-kind="dropdown"
        aria-label={label}
        aria-required={def.required || undefined}
        value={current}
        disabled={Boolean(blocked)}
        title={blocked ?? label}
        style={style}
        onChange={(event) => void onValue(record.key, event.target.value)}
      >
        <option value="">—</option>
        {(def.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        {current && !(def.options ?? []).includes(current) && <option value={current}>{current}</option>}
      </select>
    );
  }
  return (
    <TextFill
      record={record}
      value={typeof value === 'string' ? value : ''}
      style={style}
      label={label}
      blocked={blocked}
      onValue={onValue}
    />
  );
}

function TextFill({
  record,
  value,
  style,
  label,
  blocked,
  onValue,
}: {
  record: FieldRecord;
  value: string;
  style: CSSProperties;
  label: string;
  blocked: string | null;
  onValue: (key: string, value: FieldValue) => Promise<boolean> | boolean | void;
}) {
  const def = record.def;
  const multiline = def.type === 'multiline';
  const [text, setTextState] = useState(value);
  const textRef = useRef(value);
  const setText = (next: string) => {
    textRef.current = next;
    setTextState(next);
  };
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) {
      textRef.current = value;
      setTextState(value);
    }
  }, [value]);
  const bad = winAnsiProblem(text, multiline);

  const commit = () => {
    const current = textRef.current;
    if (current === value) return;
    if (winAnsiProblem(current, multiline)) return; // said below the box; nothing is sent
    void onValue(record.key, multiline ? current : current.replace(/\n/g, ' '));
  };
  const common = {
    className: `fm-fill fm-fill--text${bad ? ' has-charset-problem' : ''}`,
    'data-testid': 'field-input',
    'data-kind': def.type,
    'aria-label': label,
    'aria-required': def.required || undefined,
    'aria-invalid': bad ? true : undefined,
    value: text,
    readOnly: Boolean(blocked),
    title: blocked ?? label,
    maxLength: def.max_len ?? undefined,
    style,
    onFocus: () => {
      focused.current = true;
    },
    onBlur: () => {
      focused.current = false;
      commit();
    },
  };
  return (
    <>
      {multiline ? (
        <textarea
          {...common}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              setText(value);
              (event.target as HTMLTextAreaElement).blur();
            } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              (event.target as HTMLTextAreaElement).blur();
            }
          }}
        />
      ) : (
        <input
          type="text"
          {...common}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              (event.target as HTMLInputElement).blur();
            } else if (event.key === 'Escape') {
              setText(value);
              (event.target as HTMLInputElement).blur();
            }
          }}
        />
      )}
      {bad && (
        <span className="fm-warn" role="alert" data-testid="field-charset-warning">
          {charsetMessage(bad)}
        </span>
      )}
    </>
  );
}
