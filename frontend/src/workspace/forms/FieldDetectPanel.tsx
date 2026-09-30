import type { FieldType } from '../../api/forms';
import { FIELD_TYPES, FIELD_TYPE_LABEL } from './fieldModel';
import type { Suggestion } from './FieldLayer';

export type { Suggestion };

/**
 * F2b: «Detect form fields» (EDITOR-V2-PLAN §7.4 `FieldDetectPanel.tsx`).
 *
 * The server (`POST …/detect-fields {page_uids}`) looks for blanks, boxes and
 * signature lines on the pages ON SCREEN only (a whole 44-page package takes
 * ~6 s) and answers suggestions -- nothing is stored. Each one is a dashed
 * frame on its page (`field-suggestion`) that can be moved, resized and
 * retyped here; nothing is added silently: Accept (one undo step), Reject,
 * or Accept all (ONE undo step for all of them).
 */

interface Props {
  status: 'idle' | 'loading' | 'ready' | 'error';
  error: string | null;
  pages: readonly number[];
  items: readonly Suggestion[];
  picked: string | null;
  onPick: (id: string | null) => void;
  onType: (id: string, type: FieldType) => void;
  onAccept: (id: string) => void;
  onReject: (id: string) => void;
  onAcceptAll: () => void;
  onRun: () => void;
  onClose: () => void;
}

export function FieldDetectPanel({
  status,
  error,
  pages,
  items,
  picked,
  onPick,
  onType,
  onAccept,
  onReject,
  onAcceptAll,
  onRun,
  onClose,
}: Props) {
  const pageText = pages.length === 0 ? '' : pages.length === 1 ? `page ${pages[0]}` : `pages ${pages.join(', ')}`;
  return (
    <section className="fm-dock fm-detect" data-testid="detect-panel" data-status={status} role="region" aria-label="Detect form fields">
      <header className="fm-props__head">
        <strong className="fm-props__title">Detect form fields</strong>
        <button type="button" className="fm-props__close" aria-label="Close detection" title="Close (back to Select)" onClick={onClose}>
          ×
        </button>
      </header>
      <p className="fm-dock__hint" data-testid="detect-status" role="status">
        {status === 'loading'
          ? `Looking for form fields on ${pageText}…`
          : status === 'error'
            ? error
            : status === 'ready'
              ? items.length === 0
                ? `No new form fields were found on ${pageText}.`
                : `${items.length} suggestion${items.length === 1 ? '' : 's'} on ${pageText}. Nothing is added until you accept.`
              : ''}
      </p>
      {items.length > 0 && (
        <ol className="fm-detect__list">
          {items.map((item) => (
            <li
              key={item.suggestion_id}
              className={`fm-detect__row${item.suggestion_id === picked ? ' is-picked' : ''}`}
              data-testid="detect-row"
              data-suggestion-id={item.suggestion_id}
              onMouseEnter={() => onPick(item.suggestion_id)}
            >
              <span className="fm-detect__label" title={item.label}>
                {item.label || item.name}
              </span>
              <select
                className="fm-props__input fm-detect__type"
                data-testid="suggestion-type"
                aria-label={`Field type for «${item.label || item.name}»`}
                value={item.type}
                onChange={(event) => onType(item.suggestion_id, event.target.value as FieldType)}
              >
                {FIELD_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {FIELD_TYPE_LABEL[type]}
                  </option>
                ))}
              </select>
              <span className="fm-detect__confidence" title="How sure the detector is">
                {Math.round(item.confidence * 100)}%
              </span>
              <button
                type="button"
                className="sj-btn sj-btn--primary fm-detect__btn"
                data-testid="detect-accept"
                aria-label={`Accept «${item.label || item.name}»`}
                onClick={() => onAccept(item.suggestion_id)}
              >
                Accept
              </button>
              <button
                type="button"
                className="sj-btn fm-detect__btn"
                data-testid="detect-reject"
                aria-label={`Reject «${item.label || item.name}»`}
                onClick={() => onReject(item.suggestion_id)}
              >
                Reject
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="fm-props__actions">
        <button
          type="button"
          className="sj-btn sj-btn--primary"
          data-testid="detect-accept-all"
          disabled={items.length === 0 || status !== 'ready'}
          onClick={onAcceptAll}
        >
          Accept all{items.length > 0 ? ` (${items.length})` : ''}
        </button>
        <button type="button" className="sj-btn" data-testid="detect-run" disabled={status === 'loading'} onClick={onRun}
          title="Look again on the pages on screen now">
          Detect again
        </button>
      </div>
    </section>
  );
}
