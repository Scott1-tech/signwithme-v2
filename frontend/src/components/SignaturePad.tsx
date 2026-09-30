import type { ReactNode } from 'react';
import { useSignature } from '../hooks/useSignature';

interface Props {
  onConfirm: (dataUrl: string) => void;
  onCancel: () => void;
  title?: string;
  busy?: boolean;
  // Optional controls rendered between the pad and the action row -- used by the
  // signing page to offer "Save to my signatures" without this component having
  // to know anything about the signature library.
  extra?: ReactNode;
}

export function SignaturePad({ onConfirm, onCancel, title = 'Draw your signature', busy, extra }: Props) {
  const pad = useSignature();

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal__panel">
        <h2 className="modal__title">{title}</h2>
        <p className="modal__hint">Sign inside the box using a mouse, trackpad or touchscreen.</p>

        <div className="signature-pad">
          <canvas ref={pad.canvasRef} className="signature-pad__canvas" />
          <div className="signature-pad__baseline" />
        </div>

        {extra && <div className="modal__extra">{extra}</div>}

        <div className="modal__actions">
          <button type="button" className="button button--ghost" onClick={pad.clear}>
            Clear
          </button>
          <span className="modal__spacer" />
          <button type="button" className="button button--ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={pad.isEmpty || busy}
            onClick={() => {
              const dataUrl = pad.toDataUrl();
              if (dataUrl) onConfirm(dataUrl);
            }}
          >
            {busy ? 'Saving…' : 'Apply signature'}
          </button>
        </div>
      </div>
    </div>
  );
}
