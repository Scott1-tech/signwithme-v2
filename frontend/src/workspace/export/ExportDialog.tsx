import { useState } from 'react';
import { documentApi, type ExportJobOut } from '../../api/document';

/**
 * F2 «Download PDF» (EDITOR-V2-PLAN §7 «Top bar», B2 exports): the current
 * version of the document, with the annotations drawn in or not.
 *
 * «Include annotations (N)» counts EVERY annotation -- the ones hidden on
 * screen too («Hide annotations» is a view setting, never a silent removal):
 * the server draws all of the saved markup (`include_markup`). The job is
 * created (`POST …/exports`), followed until done, and its file handed to the
 * browser; the finished job is then deleted on the server.
 */

const POLL_MS = 400;
const POLL_LIMIT = 150;

export async function runExport(contractId: string, includeMarkup: boolean): Promise<ArrayBuffer> {
  let job: ExportJobOut = await documentApi.createExport(contractId, { include_markup: includeMarkup });
  for (let attempt = 0; job.status !== 'done' && attempt < POLL_LIMIT; attempt += 1) {
    if (job.status === 'failed' || job.status === 'cancelled') {
      throw new Error(job.error || `The export ${job.status}`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
    job = await documentApi.export(contractId, job.id);
  }
  if (job.status !== 'done') throw new Error('The export did not finish in time');
  const bytes = await documentApi.exportFile(contractId, job.id);
  documentApi.cancelExport(contractId, job.id).catch(() => undefined);
  return bytes;
}

interface Props {
  count: number;
  hiddenOnScreen: boolean;
  unsavedEdits: boolean;
  busy: boolean;
  error: string | null;
  onDownload: (includeMarkup: boolean) => void;
  onClose: () => void;
}

export function ExportDialog({ count, hiddenOnScreen, unsavedEdits, busy, error, onDownload, onClose }: Props) {
  const [include, setInclude] = useState(true);
  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label="Download PDF"
      data-testid="export-dialog"
      onMouseDown={(event) => {
        if (!busy && event.target === event.currentTarget) onClose();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape' && !busy) onClose();
      }}
    >
      <div className="modal__panel ws-dialog">
        <h2 className="modal__title">Download PDF</h2>
        <p className="modal__hint">The saved document as it is now. Signatures are added only by «Generate final contract».</p>
        <label className="checkbox">
          <input
            type="checkbox"
            data-testid="export-include"
            checked={include}
            disabled={busy || count === 0}
            onChange={(event) => setInclude(event.target.checked)}
          />
          <span data-testid="export-include-label">Include annotations ({count})</span>
        </label>
        {hiddenOnScreen && count > 0 && (
          <p className="hint" data-testid="export-hidden-note">
            Annotations are hidden on screen; they are still counted and {include ? 'included' : 'left out only because you untick this'}.
          </p>
        )}
        {unsavedEdits && (
          <p className="alert alert--warn" data-testid="export-unsaved-note">
            Your unsaved edits are not in this file — Save changes first to include them.
          </p>
        )}
        {error && (
          <p className="alert alert--error" role="alert" data-testid="export-error">
            {error}
          </p>
        )}
        <div className="modal__actions">
          <span className="modal__spacer" />
          <button type="button" className="button" data-testid="export-cancel" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            data-testid="export-download"
            disabled={busy}
            onClick={() => onDownload(include && count > 0)}
          >
            {busy ? 'Preparing…' : 'Download'}
          </button>
        </div>
      </div>
    </div>
  );
}
