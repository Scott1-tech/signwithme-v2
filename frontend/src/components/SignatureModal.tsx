import { useCallback, useEffect, useRef, useState } from 'react';
import { useSignature } from '../hooks/useSignature';
import { useSignatureUpload } from '../hooks/useSignatureUpload';
import { userSignaturesApi } from '../api/userSignatures';
import { isInitialsLabel } from '../lib/initials';
import type { UserSignature } from '../types/contract';
import { TYPED_SIGNATURE_MAX_CHARS, type TypedSignatureFont } from '../api/document';
import '../styles/signing.css';

/**
 * The three ways to produce a signature, now one dialog instead of three.
 *
 * Before D6b the signing page opened a *different* modal for each of these and
 * the user jumped between them ("Draw new" closed the list, "Upload an image
 * instead" closed the pad...). They are one dialog with three tabs now, and
 * every tab body stays mounted: a half-drawn signature, a chosen file and the
 * "Save to my signatures" tick all survive a trip to another tab.
 *
 * Kept from the old markup on purpose (`tests/ui/README.md`, DOM contract):
 *   * one `.modal` > `.modal__panel`, with `.modal__extra` and `.modal__actions`
 *     exactly once each -- the baseline checks read `.modal__extra` and
 *     `.modal__panel` with a *strict* locator, so a second copy would fail;
 *   * a real `<input type="file">` on the upload tab;
 *   * the wording "Background is removed automatically." and
 *     "PNG or JPEG, up to 2 MB";
 *   * a button whose accessible name is exactly "Upload an image instead".
 */
export type SignatureTab = 'saved' | 'draw' | 'upload' | 'type';

const BASE_TABS: { id: SignatureTab; label: string }[] = [
  { id: 'saved', label: 'Saved' },
  { id: 'draw', label: 'Draw' },
  { id: 'upload', label: 'Upload' },
];
/** F2: «Type» joins where the server renders typed signatures (B2). */
const TYPE_TAB: { id: SignatureTab; label: string } = { id: 'type', label: 'Type' };

const HINTS: Record<SignatureTab, string> = {
  saved: 'Pick one of your saved signatures — it is placed as soon as you choose it.',
  draw: 'Sign inside the box using a mouse, trackpad or touchscreen.',
  upload: 'PNG or JPEG, up to 2 MB. A scan or photo of your signature on plain paper works best.',
  type: 'Type your name and pick a script. The server draws it and saves it to your signatures (the typed text itself is not kept).',
};

/** F2: the scripts of `GET /api/fonts?category=signature`, as the Type tab offers them. */
export interface TypedFont {
  family: TypedSignatureFont;
  label: string;
}
export const TYPED_MAX = TYPED_SIGNATURE_MAX_CHARS;

/** Everything inside the panel that can take focus right now. */
function focusables(panel: HTMLElement): HTMLElement[] {
  const found = panel.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
      'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  // `offsetParent === null` drops everything inside a `hidden` tab body, so Tab
  // never lands on the file input of a tab nobody is looking at.
  return [...found].filter((element) => element.offsetParent !== null);
}

/**
 * One stored signature's picture. The image route is ownership-checked and
 * streams bytes, so the preview is fetched with the auth header rather than
 * pointed at by a plain `<img src>` (same approach as `MySignatures.tsx`).
 */
function StoredPreview({ signature }: { signature: UserSignature }) {
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
  return <img src={src} alt="" />;
}

interface Props {
  title: string;
  /** The current user's reusable signatures. */
  library: UserSignature[];
  /** False while the list is still on its way, so the opening tab can wait. */
  libraryLoaded: boolean;
  busy: boolean;
  onClose: () => void;
  /** A stored signature was chosen: place it (or arm the bulk panel). */
  onChooseStored: (signature: UserSignature) => void;
  /** A drawn or uploaded image was confirmed; `previewUrl` decorates only. */
  onConfirm: (dataUrl: string, previewUrl?: string) => void;
  /** Lives on the page so it survives the modal being closed and reopened. */
  saveToLibrary: boolean;
  onSaveToLibraryChange: (value: boolean) => void;
  /**
   * W5: the dialog is choosing INITIALS -- the saved ones labelled "Initials"
   * come first and are marked, the buttons say "initials", and a copy saved
   * from here is labelled "Initials" (`useSignatureChooser`).
   */
  forInitials?: boolean;
  /** F2: the Type tab's scripts; absent = no Type tab (a server without B2). */
  typedFonts?: TypedFont[] | null;
  /** F2: a typed signature was confirmed (rendered and saved by the server, then placed). */
  onTyped?: (text: string, font: TypedSignatureFont) => void;
}

export function SignatureModal({
  title,
  library: libraryIn,
  libraryLoaded,
  busy,
  onClose,
  onChooseStored,
  onConfirm,
  saveToLibrary,
  onSaveToLibraryChange,
  forInitials = false,
  typedFonts = null,
  onTyped,
}: Props) {
  const TABS = typedFonts && typedFonts.length > 0 && onTyped ? [...BASE_TABS, TYPE_TAB] : BASE_TABS;
  const [typedText, setTypedText] = useState('');
  const [typedFont, setTypedFont] = useState<TypedSignatureFont | null>(typedFonts?.[0]?.family ?? null);
  useEffect(() => {
    if (!typedFont && typedFonts?.[0]) setTypedFont(typedFonts[0].family);
  }, [typedFont, typedFonts]);
  // Initials first when initials are wanted (a stable sort keeps the rest in order).
  const library = forInitials
    ? [...libraryIn].sort((a, b) => Number(isInitialsLabel(b.label)) - Number(isInitialsLabel(a.label)))
    : libraryIn;
  const noun = forInitials ? 'initials' : 'signature';
  // Opening rule: the library if there is something in it, otherwise draw.
  const [tab, setTab] = useState<SignatureTab>(() => (library.length > 0 ? 'saved' : 'draw'));
  const chosenTab = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const tabsRef = useRef<HTMLDivElement>(null);

  const pad = useSignature();
  const upload = useSignatureUpload();
  const fileRef = useRef<HTMLInputElement>(null);

  // The list can land a moment after the dialog does. Apply the opening rule
  // once it has -- but never over a tab the user has already picked.
  useEffect(() => {
    if (chosenTab.current || !libraryLoaded) return;
    setTab(library.length > 0 ? 'saved' : 'draw');
  }, [libraryLoaded, library.length]);

  const goTo = useCallback((next: SignatureTab) => {
    chosenTab.current = true;
    setTab(next);
  }, []);

  // Focus starts inside the dialog and is handed back to whatever opened it.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const timer = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      const selected = panel.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
      (selected ?? focusables(panel)[0])?.focus();
    }, 0);
    return () => {
      window.clearTimeout(timer);
      opener?.focus?.();
    };
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const panel = panelRef.current;
    if (!panel) return;
    const items = focusables(panel);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;
    const inside = active ? panel.contains(active) : false;
    if (event.shiftKey ? active === first || !inside : active === last || !inside) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  };

  // Arrow keys move between tabs, the way a tablist is expected to behave.
  const handleTabKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = TABS.findIndex((item) => item.id === tab);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? TABS.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length;
    goTo(TABS[next].id);
    window.setTimeout(() => {
      tabsRef.current
        ?.querySelector<HTMLElement>(`[data-tab="${TABS[next].id}"]`)
        ?.focus();
    }, 0);
  };

  const resetUpload = () => {
    upload.clear();
    if (fileRef.current) fileRef.current.value = '';
  };

  const applyDrawn = () => {
    const dataUrl = pad.toDataUrl();
    if (dataUrl) onConfirm(dataUrl);
  };

  const applyUploaded = () => {
    const dataUrl = upload.toDataUrl();
    if (dataUrl) onConfirm(dataUrl, upload.toPreviewDataUrl() ?? undefined);
  };

  const saveTick = (
    <label className="checkbox">
      <input
        type="checkbox"
        checked={saveToLibrary}
        disabled={busy}
        onChange={(event) => onSaveToLibraryChange(event.target.checked)}
      />
      Save to my signatures
    </label>
  );

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onKeyDown={handleKeyDown}
      // A click on the dim area outside the panel dismisses, as every other
      // dialog in the app does; `currentTarget` keeps a click *inside* out of it.
      onMouseDown={(event) => {
        if (!busy && event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal__panel sign-modal" ref={panelRef}>
        <div className="sign-modal__head">
          <h2 className="modal__title">{title}</h2>
          <p className="modal__hint">
            {HINTS[tab]}
            {forInitials && ' Initials are placed small (inside 60 × 24 pt), centred where you clicked.'}
          </p>
        </div>

        <div
          className="sign-modal__tabs"
          role="tablist"
          aria-label="Where the signature comes from"
          ref={tabsRef}
          onKeyDown={handleTabKeys}
        >
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              data-tab={item.id}
              id={`sign-tab-${item.id}`}
              className="sign-tab"
              aria-selected={tab === item.id}
              aria-controls={`sign-panel-${item.id}`}
              tabIndex={tab === item.id ? 0 : -1}
              onClick={() => goTo(item.id)}
            >
              {item.label}
              {item.id === 'saved' && library.length > 0 && (
                <span className="sign-tab__count">{library.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* --- Saved --------------------------------------------------------- */}
        <div
          className="sign-panel"
          role="tabpanel"
          id="sign-panel-saved"
          aria-labelledby="sign-tab-saved"
          hidden={tab !== 'saved'}
        >
          {library.length > 0 ? (
            <ul className="sign-library">
              {library.map((signature) => (
                <li key={signature.id}>
                  <button
                    type="button"
                    className="sign-library__button"
                    disabled={busy}
                    onClick={() => onChooseStored(signature)}
                  >
                    <span className="sign-library__preview">
                      <StoredPreview signature={signature} />
                    </span>
                    <span className="sign-library__name">
                      {signature.label ?? 'Saved signature'}
                      {signature.is_default && <span className="badge badge--good">default</span>}
                      {isInitialsLabel(signature.label) && (
                        <span className="badge badge--initials" data-testid="sign-library-initials">
                          initials
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="sign-library__empty">
              You have no saved signatures yet. Draw one or upload an image — tick
              “Save to my signatures” and it will be waiting here next time.
            </p>
          )}
        </div>

        {/* --- Draw ---------------------------------------------------------- */}
        <div
          className="sign-panel"
          role="tabpanel"
          id="sign-panel-draw"
          aria-labelledby="sign-tab-draw"
          hidden={tab !== 'draw'}
        >
          <div className="signature-pad">
            <canvas ref={pad.canvasRef} className="signature-pad__canvas" />
            <div className="signature-pad__baseline" />
          </div>
        </div>

        {/* --- Upload -------------------------------------------------------- */}
        <div
          className="sign-panel"
          role="tabpanel"
          id="sign-panel-upload"
          aria-labelledby="sign-tab-upload"
          hidden={tab !== 'upload'}
        >
          <label className="field">
            <span>Image file</span>
            <input
              ref={fileRef}
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
        </div>

        {/* --- Type (F2) ------------------------------------------------------ */}
        {TABS.includes(TYPE_TAB) && (
          <div
            className="sign-panel sign-type"
            role="tabpanel"
            id="sign-panel-type"
            aria-labelledby="sign-tab-type"
            hidden={tab !== 'type'}
            data-testid="sign-type-panel"
          >
            <label className="field">
              <span>Your name</span>
              <input
                type="text"
                data-testid="sign-type-text"
                maxLength={TYPED_MAX}
                value={typedText}
                disabled={busy}
                onChange={(event) => setTypedText(event.target.value)}
              />
            </label>
            <div className="sign-type__fonts" role="radiogroup" aria-label="Script">
              {(typedFonts ?? []).map((font) => (
                <button
                  key={font.family}
                  type="button"
                  role="radio"
                  aria-checked={typedFont === font.family}
                  className={`sign-type__font${typedFont === font.family ? ' is-selected' : ''}`}
                  data-testid={`sign-type-font-${font.family}`}
                  title={font.label}
                  disabled={busy}
                  onClick={() => setTypedFont(font.family)}
                >
                  <span className="sign-type__preview" style={{ fontFamily: `${font.family}, cursive` }}>
                    {typedText.trim() || 'Your Name'}
                  </span>
                  <span className="sign-type__label">{font.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* One `.modal__extra` for the whole dialog: the tick belongs to the
            signature, not to the tab it was made on. */}
        <div className="modal__extra">
          {tab === 'upload' && <span className="hint">Background is removed automatically.</span>}
          {tab !== 'saved' && tab !== 'type' && saveTick}
          {tab === 'type' && <span className="hint">Saved to your signatures automatically.</span>}
          {tab === 'draw' && (
            <button
              type="button"
              className="button button--link"
              disabled={busy}
              onClick={() => goTo('upload')}
            >
              Upload an image instead
            </button>
          )}
          {tab === 'upload' && (
            <button
              type="button"
              className="button button--link"
              disabled={busy}
              onClick={() => goTo('draw')}
            >
              Draw instead
            </button>
          )}
          {tab === 'saved' && (
            <span className="hint">Choosing one places it straight away.</span>
          )}
        </div>

        <div className="modal__actions">
          {tab === 'draw' && (
            <button type="button" className="button button--ghost" disabled={busy} onClick={pad.clear}>
              Clear
            </button>
          )}
          {tab === 'upload' && (
            <button
              type="button"
              className="button button--ghost"
              disabled={busy || upload.isEmpty}
              onClick={resetUpload}
            >
              Clear
            </button>
          )}
          <span className="modal__spacer" />
          <button type="button" className="button button--ghost" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          {tab === 'draw' && (
            <button
              type="button"
              className="button button--primary"
              disabled={pad.isEmpty || busy}
              onClick={applyDrawn}
            >
              {busy ? 'Saving…' : `Apply ${noun}`}
            </button>
          )}
          {tab === 'upload' && (
            <button
              type="button"
              className="button button--primary"
              disabled={upload.isEmpty || busy}
              onClick={applyUploaded}
            >
              {busy ? 'Saving…' : `Apply ${noun}`}
            </button>
          )}
          {tab === 'type' && (
            <button
              type="button"
              className="button button--primary"
              data-testid="sign-type-apply"
              disabled={!typedText.trim() || !typedFont || busy}
              onClick={() => typedFont && onTyped?.(typedText.trim().normalize('NFC'), typedFont)}
            >
              {busy ? 'Saving…' : `Apply ${noun}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
