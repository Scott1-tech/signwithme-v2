import { useRef, type ReactNode } from 'react';
import { useSignatureUpload } from '../hooks/useSignatureUpload';

interface Props {
  /**
   * `dataUrl` is the original file (what must be uploaded); `previewUrl` is
   * the client-cleaned PNG, for display only.
   */
  onConfirm: (dataUrl: string, previewUrl?: string) => void;
  onCancel: () => void;
  title?: string;
  busy?: boolean;
  /** Same slot as on `SignaturePad`: extra controls above the action row. */
  extra?: ReactNode;
  confirmLabel?: string;
}

/**
 * Upload-an-image counterpart to `SignaturePad`. Same modal shape, same
 * `onConfirm(dataUrl)` contract, so callers can swap one for the other.
 */
export function SignatureUploadPad({
  onConfirm,
  onCancel,
  title = 'Upload your signature',
  busy,
  extra,
  confirmLabel = 'Apply signature',
}: Props) {
  const upload = useSignatureUpload();
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    upload.clear();
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal__panel">
        <h2 className="modal__title">{title}</h2>
        <p className="modal__hint">
          PNG or JPEG, up to 2 MB. A scan or photo of your signature on plain paper works best.
        </p>

        <label className="field">
          <span>Image file</span>
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg"
            disabled={busy}
            onChange={(event) => void upload.select(event.target.files?.[0] ?? null)}
          />
        </label>

        {upload.error && <p className="alert alert--error">{upload.error}</p>}

        <div className={`upload-preview${upload.isEmpty ? ' upload-preview--empty' : ''}`}>
          <canvas
            ref={upload.canvasRef}
            className="upload-preview__canvas"
            aria-label="Signature preview"
          />
          {upload.isEmpty && <span className="hint">Preview appears here</span>}
        </div>

        <div className="modal__extra">
          <span className="hint">Background is removed automatically.</span>
          {extra}
        </div>

        <div className="modal__actions">
          <button
            type="button"
            className="button button--ghost"
            disabled={busy || upload.isEmpty}
            onClick={reset}
          >
            Clear
          </button>
          <span className="modal__spacer" />
          <button type="button" className="button button--ghost" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={upload.isEmpty || busy}
            onClick={() => {
              const dataUrl = upload.toDataUrl();
              if (dataUrl) onConfirm(dataUrl, upload.toPreviewDataUrl() ?? undefined);
            }}
          >
            {busy ? 'Saving…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
