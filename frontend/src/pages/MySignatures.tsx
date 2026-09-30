import { useCallback, useEffect, useState } from 'react';
import { SignaturePad } from '../components/SignaturePad';
import { SignatureUploadPad } from '../components/SignatureUploadPad';
import { describeSaveError, userSignaturesApi } from '../api/userSignatures';
import { isInitialsLabel } from '../lib/initials';
import { formatServerDateTime } from '../lib/time';
import type { UserSignature } from '../types/contract';

type AddMode = 'draw' | 'upload' | null;

/**
 * One stored signature's picture. The image route is ownership-checked and
 * streams bytes, so each preview is fetched with the auth header via
 * `imageAsDataUrl` rather than pointed at by a plain `<img src>`.
 */
function SignaturePreview({ signature }: { signature: UserSignature }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setFailed(false);
    userSignaturesApi
      .imageAsDataUrl(signature.id)
      .then((dataUrl) => {
        if (!cancelled) setSrc(dataUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [signature.id]);

  if (failed) return <span className="hint hint--error">Preview unavailable</span>;
  if (!src) return <span className="hint">Loading…</span>;
  return <img src={src} alt={signature.label ?? 'Saved signature'} />;
}

export function MySignatures() {
  const [signatures, setSignatures] = useState<UserSignature[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<AddMode>(null);
  const [label, setLabel] = useState('');

  const refresh = useCallback(async () => {
    setSignatures(await userSignaturesApi.list());
  }, []);

  useEffect(() => {
    refresh()
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : 'Failed to load your signatures'),
      )
      .finally(() => setLoading(false));
  }, [refresh]);

  const open = (next: Exclude<AddMode, null>) => {
    setError(null);
    setLabel('');
    setMode(next);
  };

  // Shared by both the draw and upload modals: each hands over a PNG data URL
  // in exactly the shape the signing page already POSTs as `image_data`.
  const save = async (dataUrl: string) => {
    setBusy(true);
    setError(null);
    try {
      await userSignaturesApi.save({
        image_data: dataUrl,
        label: label.trim() || null,
      });
      setMode(null);
      setLabel('');
      await refresh();
    } catch (cause) {
      setError(describeSaveError(cause));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (signature: UserSignature) => {
    const name = signature.label ?? 'this signature';
    if (!window.confirm(`Delete ${name}? This cannot be undone.`)) return;
    setBusy(true);
    setError(null);
    try {
      await userSignaturesApi.remove(signature.id);
      setSignatures((current) => current.filter((item) => item.id !== signature.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete the signature');
    } finally {
      setBusy(false);
    }
  };

  const labelField = (
    <label className="field field--inline">
      <span>Name (optional)</span>
      <input
        value={label}
        maxLength={255}
        disabled={busy}
        placeholder="e.g. Full signature — or “Initials” to offer it first for initials"
        onChange={(event) => setLabel(event.target.value)}
      />
    </label>
  );

  return (
    <section className="page">
      <header className="page__header">
        <h1>My signatures</h1>
        <div className="table__actions">
          <button type="button" className="button" disabled={busy} onClick={() => open('upload')}>
            Upload image
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={busy}
            onClick={() => open('draw')}
          >
            Draw new
          </button>
        </div>
      </header>

      <p className="hint">
        Saved signatures are private to you and can be reused on any contract you sign.
      </p>

      {error && !mode && <p className="alert alert--error">{error}</p>}
      {loading && <p>Loading…</p>}
      {!loading && signatures.length === 0 && (
        <p className="empty">No saved signatures yet. Draw one or upload an image to get started.</p>
      )}

      {signatures.length > 0 && (
        <div className="sig-grid">
          {signatures.map((signature) => (
            <article key={signature.id} className="card sig-card">
              <div className="sig-card__preview">
                <SignaturePreview signature={signature} />
              </div>
              <div className="sig-card__row">
                <div>
                  <div className="sig-card__title">
                    {signature.label ?? 'Saved signature'}
                    {signature.is_default && <span className="badge badge--good">default</span>}
                    {/* W5: saved from the Initials tool (its label; the library has no kind). */}
                    {isInitialsLabel(signature.label) && (
                      <span
                        className="badge badge--initials"
                        data-testid="sig-card-initials"
                        title="Saved as initials — offered first when you place initials"
                      >
                        initials
                      </span>
                    )}
                  </div>
                  <div className="sig-card__meta">
                    Added {formatServerDateTime(signature.created_at)}
                  </div>
                </div>
                <button
                  type="button"
                  className="button button--link"
                  disabled={busy}
                  onClick={() => void remove(signature)}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {mode === 'draw' && (
        <SignaturePad
          title="Draw a signature"
          busy={busy}
          onCancel={() => setMode(null)}
          onConfirm={(dataUrl) => void save(dataUrl)}
          extra={
            <>
              {labelField}
              {error && <p className="alert alert--error">{error}</p>}
            </>
          }
        />
      )}

      {mode === 'upload' && (
        <SignatureUploadPad
          title="Upload a signature image"
          busy={busy}
          confirmLabel="Save signature"
          onCancel={() => setMode(null)}
          onConfirm={(dataUrl) => void save(dataUrl)}
          extra={
            <>
              {labelField}
              {error && <p className="alert alert--error">{error}</p>}
            </>
          }
        />
      )}
    </section>
  );
}
