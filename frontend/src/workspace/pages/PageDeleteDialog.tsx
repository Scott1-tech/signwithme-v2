/**
 * F2: «Delete page N?» -- says what goes with it BEFORE anything is changed:
 * the signatures placed on it, the signature / date slots of the template on
 * it, its links and its annotations. A page with slots can only be deleted
 * by a reviewer or an admin (the server answers 403 `page_has_slots` to the
 * uploader, and the review then flags `page_removed_with_slots`): the dialog
 * says so instead of offering a button that would fail.
 */

export interface PageDeleteCounts {
  signatures: number;
  slots: number;
  links: number | null;
  annotations: number;
}

interface Props {
  page: number;
  counts: PageDeleteCounts;
  /** Why this user may not delete it (slots, not a reviewer), or null. */
  refusal: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

export function PageDeleteDialog({ page, counts, refusal, onConfirm, onClose }: Props) {
  const parts: string[] = [];
  if (counts.signatures) parts.push(`${counts.signatures} signature${counts.signatures === 1 ? '' : 's'}`);
  if (counts.slots) parts.push(`${counts.slots} signature / date slot${counts.slots === 1 ? '' : 's'}`);
  if (counts.links) parts.push(`${counts.links} link${counts.links === 1 ? '' : 's'}`);
  if (counts.annotations) parts.push(`${counts.annotations} annotation${counts.annotations === 1 ? '' : 's'}`);
  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label={`Delete page ${page}`}
      data-testid="page-delete-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') onClose();
      }}
    >
      <div className="modal__panel ws-dialog">
        <h2 className="modal__title">Delete page {page}?</h2>
        <p
          className="modal__hint"
          data-testid="page-delete-counts"
          data-signatures={counts.signatures}
          data-slots={counts.slots}
          data-links={counts.links ?? ''}
          data-annotations={counts.annotations}
        >
          {parts.length
            ? `On this page: ${parts.join(', ')}. They go with the page when you save.`
            : 'Nothing is placed on this page.'}{' '}
          Deleting a page re-opens the review. Undo brings it back until you save.
        </p>
        {refusal && (
          <p className="alert alert--warn" role="alert" data-testid="page-delete-reason">
            {refusal}
          </p>
        )}
        <div className="modal__actions">
          <span className="modal__spacer" />
          <button type="button" className="button" data-testid="page-delete-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--danger"
            data-testid="page-delete-confirm"
            disabled={Boolean(refusal)}
            onClick={onConfirm}
          >
            Delete page
          </button>
        </div>
      </div>
    </div>
  );
}
