import type { FinalizeMissing } from '../lib/signing';

interface Props {
  error: string | null;
  /** What a finalize 409 said was still missing, if that is what the error is. */
  missing: FinalizeMissing | null;
  /** "Show": point the page at that slot. */
  onShow: (fieldId: string) => void;
}

/**
 * The workspace's one error line (W1: the signing page's and the editor's
 * `editor-error` are the same block now), with the slots a refused finalize
 * still wants listed under it, each with a "Show". It stays in the document
 * column (`.review__document .alert--error`), above the viewer.
 */
export function FinalizeErrors({ error, missing, onShow }: Props) {
  if (!error) return null;
  return (
    <div className="alert alert--error" data-testid="editor-error">
      <p>{error}</p>
      {missing && (
        <ul className="list">
          {missing.signatures.map((slot) => (
            <li key={`sig:${slot.field_id}`} className="list__item">
              <span>
                Signature needed: {slot.label}
                {slot.page ? ` · p.${slot.page}` : ''}
              </span>
              <button
                type="button"
                className="button button--link"
                onClick={() => onShow(slot.field_id)}
              >
                Show
              </button>
            </li>
          ))}
          {missing.dates.map((slot) => (
            <li key={`date:${slot.field_id}`} className="list__item">
              <span>
                Choose the date: {slot.label}
                {slot.page ? ` · p.${slot.page}` : ''}
              </span>
              <button
                type="button"
                className="button button--link"
                onClick={() => onShow(slot.field_id)}
              >
                Show
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
